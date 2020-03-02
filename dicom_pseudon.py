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
import os
import shutil
import argparse
import csv
import logging
import re
import sqlite3
from hashlib import md5
from tqdm import tqdm
from threading import Thread, Lock
from queue import Queue, Empty


INDEXED_LOCK_FNAME = 'indexed.lock'
TABLE_EXISTS = 'SELECT name FROM sqlite_master WHERE name=?'

TABLE_NAME = 'accession_numbers'
CREATE_TABLE = 'CREATE TABLE %s (id INTEGER PRIMARY KEY AUTOINCREMENT, original, serial, UNIQUE(original))' % TABLE_NAME
INSERT = 'INSERT OR IGNORE INTO %s (original) VALUES (?)' % TABLE_NAME
UPDATE = 'UPDATE %s SET serial = ? WHERE original = ?' % TABLE_NAME
GET = 'SELECT serial FROM %s WHERE original = ?' % TABLE_NAME
SEARCH = 'SELECT original FROM %s WHERE original LIKE ?' % TABLE_NAME

HASH_TABLE_NAME = 'fingerprints'
CREATE_HASH_TABLE = 'CREATE TABLE %s (id INTEGER PRIMARY KEY AUTOINCREMENT, hash, UNIQUE(hash))' % HASH_TABLE_NAME
INSERT_HASH = 'INSERT OR IGNORE INTO %s (hash) VALUES (?)' % HASH_TABLE_NAME
GET_HASH = 'SELECT hash FROM %s WHERE hash = ?' % HASH_TABLE_NAME

REMOVED_TEXT = 'Removed by dicom-pseudon'
DE_IDENTIFICATION_METHOD = 'Pseudonymized by The Cancer Registry of Norway'

MEDIA_STORAGE_SOP_INSTANCE_UID = (0x2, 0x3)
ACCESSION_NUMBER = (0x8, 0x50)
SERIES_DESCR = (0x8, 0x103E)
MODALITY = (0x8, 0x60)
BURNT_IN = (0x28, 0x301)
IMAGE_TYPE = (0x8, 0x8)
MANUFACTURER = (0x8, 0x70)
MANUFACTURER_MODEL_NAME = (0x8, 0x1090)
PIXEL_DATA = (0x7FE0, 0x10)

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
  PIXEL_DATA: 1,     # Pixel Data
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
}

logger = logging.getLogger('dicom_pseudon')
logger.setLevel(logging.INFO)


class Index(object):

    def __init__(self, filename):
        self.db = sqlite3.connect(filename, check_same_thread=False)
        self.cursor = self.db.cursor()

    def close(self):
        self.db.close()

    def table_exists(self, table_name):
        self.cursor.execute(TABLE_EXISTS, (table_name,))
        results = self.cursor.fetchall()
        return len(results) > 0

    def get(self, original):
        if not self.table_exists(TABLE_NAME):
            return None

        self.cursor.execute(GET, (original,))
        results = self.cursor.fetchall()
        if len(results):
            return results[0][0]

    def search(self, original):
        if not self.table_exists(TABLE_NAME):
            return None

        self.cursor.execute(SEARCH, (original,))
        results = self.cursor.fetchall()
        if len(results):
            return results[0][0]

    def insert(self, original):
        if not self.table_exists(TABLE_NAME):
            with self.db as db:
                db.execute(CREATE_TABLE)

        with self.db as db:
            db.execute(INSERT, (original,))

    def update(self, original, serial):
        with self.db as db:
            db.execute(UPDATE, (serial, original,))

    def get_hash(self, hash):
        if not self.table_exists(HASH_TABLE_NAME):
            return None

        self.cursor.execute(GET_HASH, (hash,))
        results = self.cursor.fetchall()
        if len(results):
            return results[0][0]

    def insert_hash(self, hash):
        if not self.table_exists(HASH_TABLE_NAME):
            with self.db as db:
                db.execute(CREATE_HASH_TABLE)

        with self.db as db:
            db.execute(INSERT_HASH, (hash,))


class DicomPseudon(object):
    def __init__(self, white_list_file, **kwargs):
        self.white_list_file = white_list_file
        self.index_file = kwargs.get('index_file', 'index.db')
        self.quarantine = kwargs.get('quarantine', 'quarantine')
        self.log_file = kwargs.get('log_file', 'dicom_pseudon.log')
        self.modalities = [string.lower() for string in kwargs.get('modalities', ['mr', 'ct'])]
        skip_first_line = kwargs.get('white_list_skip_first_line', False)
        is_test = kwargs.get('is_test', False)

        try:
            content = self.load_white_list(white_list_file, skip_first_line)
            self.white_list = self.parse_white_list(content)
        except IOError:
            raise Exception('Could not open white list file.')

        self.index = Index(self.index_file)

        # Skip logging handlers for tests
        if is_test:
            return

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
        self.index.close()

    @staticmethod
    def destination(source, dest, root):
        if dest.startswith(root):
            raise Exception('Destination directory cannot be inside or equal to source directory')
        if not source.startswith(root):
            raise Exception('The file to be moved must be in the root directory')
        return os.path.normpath(dest)

    def quarantine_file(self, filepath, ident_dir, reason):
        full_quarantine_dir = self.destination(filepath, self.quarantine, ident_dir)
        if not os.path.exists(full_quarantine_dir):
            os.makedirs(full_quarantine_dir)
        quarantine_name = os.path.join(full_quarantine_dir, os.path.basename(filepath))
        logger.info('%s will be moved to quarantine directory due to: %s' % (filepath, reason))
        shutil.copyfile(filepath, quarantine_name)

    # Checks (from https://wiki.cancerimagingarchive.net/download/attachments/
    # 3539047/pixel-checker-filter.script?version=1&modificationDate=1333114118541&api=v2):
    # - ImageType to ensure it does not contain the word SAVE to avoid screen saves/captures
    # - Manufacturer to ensure it's not NAI, http://www.naitechproducts.com/dicombox.html
    # - Manufacturer to ensure it's not PACSGEAR, http://www.pacsgear.com/
    # - Series Description to ensure it does not contain the word SAVE to avoid screen saves/captures
    # - Manufacturer to ensure it's not NAI, http://www.naitechproducts.com/dicombox.html
    # - If BurnedInAnnotation contains YES
    def check_quarantine(self, ds):
        if SERIES_DESCR in ds and ds[SERIES_DESCR].value is not None:
            series_desc = ds[SERIES_DESCR].value.strip().lower()
            if 'patient protocol' in series_desc:
                return True, 'patient protocol'
            elif 'save' in series_desc:
                return True, 'Likely screen capture'

        if MODALITY in ds:
            modality = ds[MODALITY]
            if modality.VM == 1:
                modality = [modality.value]
            for m in modality:
                if m is None or not m.lower() in self.modalities:
                    return True, 'modality not allowed'

        if MODALITY not in ds:
            return True, 'Modality missing'

        if BURNT_IN in ds and ds[BURNT_IN].value is not None:
            burnt_in = ds[BURNT_IN].value
            if burnt_in.strip().lower() in ['yes', 'y']:
                return True, 'burnt-in data'

        if IMAGE_TYPE in ds:
            image_type = ds[IMAGE_TYPE]
            if image_type.VM == 1:
                image_type = [image_type.value]
            for i in image_type:
                if i is not None and 'save' in i.strip().lower():
                    return True, 'Likely screen capture'

        if MANUFACTURER in ds:
            manufacturer = ds[MANUFACTURER].value.strip().lower()
            if 'north american imaging, inc' in manufacturer or 'pacsgear' in manufacturer:
                return True, 'Manufacturer is suspect'

        if MANUFACTURER_MODEL_NAME in ds:
            model_name = ds[MANUFACTURER_MODEL_NAME].value.strip().lower()
            if 'the dicom box' in model_name:
                return True, 'Manufacturer model name is suspect'

        return False, ''

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

    @staticmethod
    def get_fingerprint(ds, preserve_bytes=True):
        bytes = None
        if preserve_bytes:
            bytes = ds.PixelData

        ds[PIXEL_DATA].value = ''
        json = ds.to_json().encode('utf-8')
        return md5(json).hexdigest(), bytes

    def white_list_handler(self, e):
        value = self.white_list.get((e.tag.group, e.tag.element), None)
        if value:
            return True
        return False

    def index_built(self):
        return os.path.exists(INDEXED_LOCK_FNAME)

    def input_yes_or_no_prompt(self, question):
        while True:
            answer = input('%s [y/n] ' % question)
            if answer == 'y':
                return True
            elif answer == 'n':
                return False
            else:
                print("I didn't get that. Please enter 'y' (yes) or 'n' (no).")

    def prompt_skip_build_index(self):
        return self.input_yes_or_no_prompt('Index for search table found. Skip indexing?')

    def prompt_skip_prior(self, dir_name):
        return self.input_yes_or_no_prompt('Some files in %s have been pseudonymized before. Skip already pseudonymized files?' % dir_name)

    def fingerprints_exist(self):
        return self.index.table_exists(HASH_TABLE_NAME)

    def register_fingerprint(self, fingerprint, db_lock):
        try:
            db_lock.acquire()
            self.index.insert_hash(fingerprint)
        finally:
            db_lock.release()

    def fingerprint_exists(self, fingerprint, db_lock):
        try:
            db_lock.acquire()
            if self.index.get_hash(fingerprint) is not None:
                return True
            return False
        finally:
            db_lock.release()

    def clean(self, ds, e):
        cleaned = None
        white_listed = self.white_list_handler(e)

        if not white_listed:
            t = (e.tag.group, e.tag.element)
            if REQUIRED_TAGS.get(t, None):
                cleaned = ''
            elif PIXEL_MODULE_TAGS.get(t, None):
                return True
            else:
                del ds[e.tag]
                cleaned = REMOVED_TEXT

        if cleaned is not None and e.tag in ds and ds[e.tag].value is not None:
            ds[e.tag].value = cleaned

        # Tell our caller if we left this element intact
        return white_listed

    def clean_meta(self, ds, e):
        white_listed = self.white_list_handler(e)

        if ALLOWED_FILE_META.get((e.tag.group, e.tag.element), None):
            return False
        if not white_listed:
            del ds[e.tag]
        return white_listed

    def pseudonymize(self, ds, db_lock):
        accession_num = ds.AccessionNumber
        try:
            db_lock.acquire()
            serial_num = self.index.get(accession_num)
        finally:
            db_lock.release()

        if serial_num is None:
            raise ValueError('No serial number for accession number %s' % (accession_num,))

        # Fix file meta data portion
        if MEDIA_STORAGE_SOP_INSTANCE_UID in ds.file_meta:
            ds.file_meta[MEDIA_STORAGE_SOP_INSTANCE_UID].value = ds.SOPInstanceUID

        ds.file_meta.walk(self.clean_meta)
        ds.walk(partial(self.clean))

        return ds, serial_num

    def build_index_worker(self, ident_dir, queue, pbar, db_lock):
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
                except InvalidDicomError:  # DICOM formatting error
                    continue
                try:
                    db_lock.acquire()
                    self.index.insert(ds.AccessionNumber)
                finally:
                    db_lock.release()
            finally:
                queue.task_done()
                pbar.update()

    def build_index(self, ident_dir, links_file, delimiter=',', skip_first_line=False, num_workers=1):
        logger.info('Indexing accession numbers to search index')

        db_lock = Lock()
        queue = Queue()
        file_count = sum(len(files) for _, _, files in os.walk(ident_dir))
        pbar = tqdm(total=file_count)
        pbar.set_description('Indexing acc. numbers')

        for root, _, files in os.walk(ident_dir):
            for filename in files:
                queue.put((root, filename,))

        threads = []
        for _ in range(num_workers):
            t = Thread(target=self.build_index_worker,
                       args=(ident_dir, queue, pbar, db_lock,))
            t.start()
            threads.append(t)

        queue.join()

        for _ in range(num_workers):
            queue.put(None)
        for t in threads:
            t.join()

        pbar.close()

        # Keep track of potential duplicates in links file
        invitation_num_set = set()

        # Count lines in links file
        with open(links_file, 'r') as f:
            if skip_first_line is True:
                next(f, None)
            reader = csv.reader(f, delimiter=delimiter)
            line_count = len(list(reader))

        with open(links_file, 'r') as f:
            if skip_first_line is True:
                next(f, None)
            reader = csv.reader(f, delimiter=delimiter)
            logger.info('Indexing variables from links file')

            with tqdm(total=line_count) as pbar:
                pbar.set_description('Indexing links file')
                for line in reader:
                    try:
                        invitation_num, serial_num = line

                        if invitation_num in invitation_num_set:
                            logger.warning('Invitation number %s appears in links file multiple times' % invitation_num)
                            continue

                        invitation_num_set.add(invitation_num)
                        accession_num = self.index.search('%' + invitation_num + '%')

                        if accession_num is None:
                            logger.warning('Could not find accession number for invitation number %s' % invitation_num)
                            continue
                        self.index.update(accession_num, serial_num)
                    finally:
                        pbar.update()

        # Create lock file to indicate that index has been created
        try:
            open(INDEXED_LOCK_FNAME, 'w').close()
        except IOError:
            logger.error('Error writing lock file %s' % INDEXED_LOCK_FNAME)
            self.close_all()
            return

        logger.info('Indexed %d invitation numbers' % len(invitation_num_set))


    def walk_dicom(self, ident_dir, clean_dir, ds, source_path, fs_lock, db_lock, fingerprint):
        move, reason = self.check_quarantine(ds)

        if move:
            self.quarantine_file(source_path, ident_dir, reason)
            return False

        try:
            ds, serial_num = self.pseudonymize(ds, db_lock)
        except ValueError as e:
            self.quarantine_file(source_path, ident_dir,
                                 'Error running pseudonymize function. ' \
                                 'There may be no serial number for the ' \
                                 'accession number in this DICOM file. ' \
                                 'Error was: %s' % e)
            return False


        rel_destination_dir = os.path.join(clean_dir, serial_num)

        destination_dir = self.destination(source_path, rel_destination_dir, ident_dir)
        try:
            os.makedirs(destination_dir)
        except FileExistsError:
            # Do nothing
            pass

        # Set Accession Number to serial number from links file
        ds[ACCESSION_NUMBER].value = serial_num

        # Set Patient Identity Removed to YES
        t = Tag((0x12, 0x62))
        ds[t] = DataElement(t, 'CS', 'YES')

        # Set the De-identification method
        t = Tag((0x12, 0x63))
        ds[t] = DataElement(t, 'LO', DE_IDENTIFICATION_METHOD)

        try:
            fs_lock.acquire()
            count = len([name for name in os.listdir(destination_dir) \
                         if os.path.isfile(os.path.join(destination_dir, name))])
            clean_name = os.path.join(destination_dir, "%d.dcm" % (count + 1))

            try:
                ds.save_as(clean_name)
            except IOError:
                logger.error('Error writing file %s' % clean_name)
                self.close_all()
                return False
        finally:
            fs_lock.release()

        # Pseudonymization was successful, register fingerprint in database
        self.register_fingerprint(fingerprint, db_lock)

        return True

    def run_worker(self, clean_dir, ident_dir, queue, pbar, fs_lock, db_lock, counter_queue, skip_prior):
        prior = 0
        pseudonymized = 0

        while True:
            task = queue.get()
            if task is None:
                counter_queue.put((pseudonymized, prior))
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
                except InvalidDicomError:  # DICOM formatting error
                    self.quarantine_file(source_path, ident_dir, 'Could not read DICOM file.')
                    continue

                fp, bytes = self.get_fingerprint(ds)
                if skip_prior and self.fingerprint_exists(fp, db_lock):
                    # This file has been pseudonymized already, skip
                    prior += 1
                else:
                    ds[PIXEL_DATA].value = bytes  # Restore pixel data
                    if self.walk_dicom(ident_dir, clean_dir, ds, source_path, fs_lock, db_lock, fp):
                        pseudonymized += 1

            finally:
                queue.task_done()
                pbar.update()

    def run(self, ident_dir, clean_dir, num_workers=1, skip_prior=False):
        logger.info('Pseudonymizing DICOM files')

        fs_lock = Lock()
        db_lock = Lock()
        counter_queue = Queue()
        queue = Queue()
        file_count = sum(len(files) for _, _, files in os.walk(ident_dir))
        pbar = tqdm(total=file_count)
        pbar.set_description('Pseudonymizing files')

        for root, _, files in os.walk(ident_dir):
            for filename in files:
                queue.put((root, filename,))

        threads = []
        for _ in range(num_workers):
            t = Thread(target=self.run_worker,
                       args=(clean_dir, ident_dir, queue, pbar, fs_lock,
                             db_lock, counter_queue, skip_prior))
            t.start()
            threads.append(t)

        queue.join()

        for _ in range(num_workers):
            queue.put(None)
        for t in threads:
            t.join()

        prior = 0
        pseudonymized = 0
        while True:
            try:
                pz, pr = counter_queue.get_nowait()
                pseudonymized += pz
                prior += pr
            except Empty:
                break

        pbar.close()
        logger.info('Pseudonymized %d of %s DICOM files' % (pseudonymized, file_count))

        if prior > 0:
            logger.info('Skipped %d DICOM files because they were either duplicates or pseudonymized before' % prior)

        self.close_all()
        return True

    def clean_up(self):
        logger.info('Cleaning up index and database files')
        try:
            os.remove(INDEXED_LOCK_FNAME)
            os.remove(self.index_file)
        except OSError as err:
            logger.error(err)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(dest='ident_dir', type=str)
    parser.add_argument(dest='clean_dir', type=str)
    parser.add_argument(dest='links_file', type=str, help='Path to links csv file')
    parser.add_argument(dest='white_list_file', type=str, help='Path to white list csv file')
    parser.add_argument('-sw', '--white_list_skip_first_line', action='store_true', default=False,
                        help='Skip first line in white list file. Should be set if first line is a header. Defaults to false')
    parser.add_argument('-dl', '--links_delimiter', type=str, default=',',
                        help='Delimiter for values in links file. Defaults to ,')
    parser.add_argument('-sl', '--links_skip_first_line', action='store_true', default=False,
                        help='Skip first line in links file. Should be set if first line is a header. Defaults to false')
    parser.add_argument('-q', '--quarantine', type=str, default='quarantine',
                        help='Quarantine directory. Defaults to ./quarantine')
    parser.add_argument('-i', '--index_file', type=str, default='index.db',
                        help='Name of sqlite index file. Default to index.db')
    parser.add_argument('-m', '--modalities', type=str, nargs='+', default=['mr', 'ct'],
                        help='Comma separated list of allowed modalities. Defaults to mr,ct')
    parser.add_argument('-l', '--log_file', type=str, default=None,
                        help='Name of file to log messages to. Defaults to console')
    parser.add_argument('-w', '--num_workers', type=int, default=1,
                        help='Amount of worker threads. Defaults to 1')
    args = parser.parse_args()
    i_dir = args.ident_dir
    c_dir = args.clean_dir
    w_file = args.white_list_file
    l_file = args.links_file
    l_file_delim = args.links_delimiter
    l_file_skip_line = args.links_skip_first_line
    n_workers = args.num_workers
    del args.ident_dir
    del args.clean_dir
    del args.white_list_file
    del args.links_file
    del args.links_delimiter
    del args.links_skip_first_line
    del args.num_workers

    da = DicomPseudon(w_file, **vars(args))

    skip_build_index = False
    if da.index_built():
        skip_build_index = da.prompt_skip_build_index()
    if not skip_build_index:
        da.build_index(i_dir, l_file, l_file_delim, l_file_skip_line)

    skip_prior_pseudonymized = False
    if da.fingerprints_exist():
        skip_prior_pseudonymized = da.prompt_skip_prior(i_dir)
    da.run(i_dir, c_dir, n_workers, skip_prior_pseudonymized)
    da.clean_up()

    logger.info('Finished')
