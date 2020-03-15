#!/usr/bin/env python
# Dicom Pseudon - Python DICOM Pseudonymizer
# Copyright (c) 2020  Mike Voets
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# Partly based on/inspired from: https://github.com/chop-dbhi/dicom-anon


import pydicom
from pydicom.errors import InvalidDicomError
from pydicom.tag import Tag
from pydicom.dataelem import DataElement
from functools import partial
import shutil
import argparse
import os
import csv
import logging
import re
from signal import signal, SIGINT
from sys import exit
from multiprocessing import Process, Lock, Queue
from queue import Empty
from tqdm import tqdm


MEDIA_STORAGE_SOP_INSTANCE_UID = (0x2, 0x3)
ACCESSION_NUMBER = (0x8, 0x50)
SERIES_DESCR = (0x8, 0x103E)
MODALITY = (0x8, 0x60)
BURNT_IN = (0x28, 0x301)
IMAGE_TYPE = (0x8, 0x8)
MANUFACTURER = (0x8, 0x70)
MANUFACTURER_MODEL_NAME = (0x8, 0x1090)

ALLOWED_FILE_META = {  # Attributes taken from https://github.com/dicom/ruby-dicom
  MEDIA_STORAGE_SOP_INSTANCE_UID: 1,
  (0x2, 0x0): 1,     # File Meta Information Group Length
  (0x2, 0x1): 1,     # Version
  (0x2, 0x2): 1,     # Media Storage SOP Class UID
  (0x2, 0x10): 1,    # Transfer Syntax UID
  (0x2, 0x12): 1,    # Implementation Class UID
  (0x2, 0x13): 1     # Implementation Version Name
}

REQUIRED_TAGS = {  # Attributes taken from https://www.pclviewer.com/help/required_dicom_tags.htm
  ACCESSION_NUMBER: 1,
  (0x8, 0x20): 1,    # Study date
  (0x8, 0x30): 1,    # Study time
  (0x8, 0x90): 1,    # Referring Physician's Name
  (0x10, 0x10): 1,   # Patient's name
  (0x10, 0x20): 1,   # Patient's ID
  (0x10, 0x30): 1,   # Patietn's date of birth
  (0x10, 0x40): 1,   # Patient's sex
  (0x20, 0x10): 1,   # Study ID
  (0x20, 0x11): 1,   # Series number
  (0x20, 0x13): 1,   # Instance Number
  (0x20, 0x20): 1,   # Patient orientation
  (0x20, 0xD): 1,    # Study UID
  (0x20, 0xE): 1,    # Series UID
}

PIXEL_MODULE_TAGS = {  # Attributes taken from http://dicom.nema.org/medical/Dicom/2016a/output/chtml/part03/sect_C.7.6.3.html
  (0x28, 0x2): 1,    # Samples per Pixel
  (0x28, 0x4): 1,    # Photometric Interpretation
  (0x28, 0x6): 1,    # Planar Configuration
  (0x28, 0x10): 1,   # Rows
  (0x28, 0x11): 1,   # Columns
  (0x28, 0x34): 1,   # Pixel Aspect Ratio
  (0x28, 0x100): 1,  # Bits Allocated
  (0x28, 0x101): 1,  # Bits Stored
  (0x28, 0x102): 1,  # High Bit
  (0x28, 0x103): 1,  # Pixel Representation
  (0x28, 0x106): 1,  # Smallest Image Pixel Value
  (0x28, 0x107): 1,  # Largest Image Pixel Value
  (0x28, 0x121): 1,  # Pixel Padding Range Limit
  (0x28, 0x1101): 1, # Red Palette Color Lookup Table Descriptor
  (0x28, 0x1102): 1, # Green Palette Color Lookup Table Descriptor
  (0x28, 0x1103): 1, # Blue Palette Color Lookup Table Descriptor
  (0x28, 0x1201): 1, # Red Palette Color Lookup Table Data
  (0x28, 0x1202): 1, # Green Palette Color Lookup Table Data
  (0x28, 0x1203): 1, # Blue Palette Color Lookup Table Data
  (0x28, 0x2000): 1, # ICC Profile
  (0x28, 0x2002): 1, # Color Space
  (0x28, 0x7FE0): 1, # Pixel Data Provider URL
  (0x7FE0, 0x10): 1, # Pixel Data
}

ADDED_TAGS = {
  (0x12, 0x62): 1,   # Patient Identity Removed
  (0x12, 0x63): 1,   # De-identification Method
}

logger = logging.getLogger('dicom_pseudon')
logger.setLevel(logging.INFO)


def pbar_listener(q, total, description):
    with tqdm(total=total) as pbar:
        pbar.set_description(description)
        for item in iter(q.get, None):
            pbar.update()


def initialize_pbar_proc(total, description):
    q = Queue()
    p = Process(target=pbar_listener, args=(q, total, description,))
    return p, q


class ValidateDicomPseudon(object):
    def __init__(self, white_list_file, **kwargs):
        self.white_list_file = white_list_file
        self.log_file = kwargs.get('log_file', 'dicom_pseudon.log')
        skip_first_line = kwargs.get('white_list_skip_first_line', False)

        try:
            content = self.load_white_list(white_list_file, skip_first_line)
            self.white_list = self.parse_white_list(content)
        except IOError:
            raise Exception('Could not open white list file.')

        logger.handlers = []
        if not self.log_file:
            self.log = logging.StreamHandler()
        else:
            self.log = logging.FileHandler(self.log_file)

        self.log.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self.log.setFormatter(formatter)
        logger.addHandler(self.log)

    def close_all(self):
        if self.log_file:
            self.log.flush()
            self.log.close()

    @staticmethod
    def destination(source, dest, root):
        if dest.startswith(root):
            raise Exception('Destination directory cannot be inside or equal to source directory')
        if not source.startswith(root):
            raise Exception('The file to be moved must be in the root directory')
        return os.path.normpath(dest)

    @staticmethod
    def load_white_list(fn, skip_first_line=False):
        with open(fn, 'r') as f:
            reader = csv.reader(f)
            if skip_first_line is True:
                next(f, None)
            return [','.join(row) for row in reader]


    @staticmethod
    def parse_white_list(content):
        values = {}
        for tag in content:
            a, b = re.sub(r'[\(\)]', '', tag).split(',')
            t = (int(a, 16), int(b, 16))
            values[t] = 1
        return values

    def white_list_handler(self, e):
        value = self.white_list.get((e.tag.group, e.tag.element), None)
        if value:
            return True
        return False

    def validate_tags(self, ds, e):
        white_listed = self.white_list_handler(e)

        if not white_listed:
            t = (e.tag.group, e.tag.element)
            if REQUIRED_TAGS.get(t, None) or PIXEL_MODULE_TAGS.get(t, None) or ADDED_TAGS.get(t, None):
                return True
            else:
                logger.error('Tag %s not removed from file (AccessionNumber: %s)' % (t, ds.AccessionNumber))
                return False
        return True

    def validate_meta_tags(self, ds, e):
        white_listed = self.white_list_handler(e)

        if ALLOWED_FILE_META.get((e.tag.group, e.tag.element), None):
            return True
        if not white_listed:
            logger.error('Tag %s not removed from file (AccessionNumber: %s)' % (t, ds.AccessionNumber))
            return False
        return True

    def validate(self, ds):
        ds.file_meta.walk(self.validate_meta_tags)
        ds.walk(partial(self.validate_tags))

        return ds

    def run_worker(self, queue, pbar_queue):
        while True:
            task = queue.get()
            if task is None:
                break

            root, filename = task
            try:
                if filename.startswith('.'):
                    continue
                source_path = os.path.join(root, filename)
                ds = None
                try:
                    ds = pydicom.read_file(source_path)
                except IOError:
                    logger.error('Error reading file %s' % source_path)
                    self.close_all()
                    return False
                self.validate(ds)
            finally:
                pbar_queue.put(1)

    def run(self, clean_dir, num_workers=1):
        logger.info('Validating pseudonymized DICOM files')

        queue = Queue()
        file_count = sum(len(files) for _, _, files in os.walk(clean_dir))

        pbar_p, pbar_q = initialize_pbar_proc(total=file_count,
                                              description='Validating files')
        pbar_p.start()

        for root, _, files in os.walk(clean_dir):
            for filename in files:
                queue.put((root, filename,))

        processes = []
        for _ in range(num_workers):
            p = Process(target=self.run_worker, args=(queue, pbar_q,))
            processes.append(p)
            p.daemon = True
            p.start()

        for _ in range(num_workers):
            queue.put(None)
        for p in processes:
            p.join()

        pbar_q.put(None)
        pbar_p.join()

        logger.info('Validated %s pseudonymized DICOM files' % file_count)
        self.close_all()
        return True


def exit_handler(signal_received, frame):
    print('Exited gracefully')
    exit(0)


if __name__ == '__main__':
    signal(SIGINT, exit_handler)

    parser = argparse.ArgumentParser()
    parser.add_argument(dest='clean_dir', type=str)
    parser.add_argument(dest='white_list_file', type=str, help='Path to white list csv file')
    parser.add_argument('-sw', '--white_list_skip_first_line', action='store_true', default=False,
                        help='Skip first line in white list file. Should be set if first line is a header. Defaults to false')
    parser.add_argument('-l', '--log_file', type=str, default=None,
                        help='Name of file to log messages to. Defaults to console')
    parser.add_argument('-w', '--num_workers', type=int, default=1,
                        help='Amount of worker processes. Defaults to 1')
    args = parser.parse_args()
    c_dir = args.clean_dir
    w_file = args.white_list_file
    n_workers = args.num_workers
    del args.clean_dir
    del args.white_list_file
    del args.num_workers
    da = ValidateDicomPseudon(w_file, **vars(args))
    da.run(c_dir, n_workers)
