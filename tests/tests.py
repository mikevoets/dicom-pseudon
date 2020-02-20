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


import unittest
import pydicom
from pydicom.errors import InvalidDicomError
import dicom_pseudon
import csv
import random
import re
import os
import shutil

# Backwards compability for secrets method in Python < 3.6
try:
    from secrets import token_hex
except ImportError:
    from os import urandom
    def token_hex(nbytes=None):
        return urandom(nbytes).hex()


ACCESSION_NUMBER = (0x8, 0x50)
IMAGE_LATERALITY = (0x20, 0x62)
PIXEL_DATA = (0x7FE0, 0x10)
STUDY_UID = (0x20, 0xD)
STATION_NAME = (0x8, 0x1010)


def walk_dicoms(dir):
    for root, _, files in os.walk(dir):
        for filename in files:
            if filename.startswith('.'):
                continue
            source_path = os.path.join(root, filename)
            try:
                yield pydicom.read_file(source_path), source_path
            except IOError:
                return False
            except InvalidDicomError:  # DICOM formatting error
                continue


class TestDicomPseudon(unittest.TestCase):

    def setUp(self):
        acc_set = set()

        # Create a links csv file from data in /samples
        with open("tests/links.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(['Invitasjonsnummer', 'Loepenummer'])

            for ds, _ in walk_dicoms("tests/samples"):
                acc = ds.AccessionNumber

                if acc in acc_set:
                    continue

                acc_set.add(acc)
                start = random.randint(0, int(len(acc)/4))
                end = -random.randint(1, int(len(acc)/4))
                serial_num = token_hex(10)
                writer.writerow([acc[start:end], serial_num])

        # Prepare instance and build index
        dp = dicom_pseudon.DicomPseudon("tests/white_list.csv",
                                        white_list_skip_first_line=True,
                                        quarantine="tests/quarantine",
                                        index_file="tests/index.db",
                                        modalities=["mg"], log_file=None,
                                        is_test=True)
        dp.build_index("tests/samples", "tests/links.csv", skip_first_line=True)
        dp.run("tests/samples", "tests/clean")

        self.orig = pydicom.read_file("tests/samples/1/1_lbm/1.dcm")
        self.sernum = self.getSerialNumber("R9BF8PC1GE")
        self.pseu = pydicom.read_file("tests/clean/%s/1.dcm" % self.sernum)

    def tearDown(self):
        if os.path.isfile("tests/links.csv"):
            os.remove("tests/links.csv")
        if os.path.isfile("tests/index.db"):
            os.remove("tests/index.db")
        if os.path.exists("tests/clean"):
            shutil.rmtree("tests/clean")
        if os.path.exists("tests/quarantine"):
            shutil.rmtree("tests/quarantine")

    @staticmethod
    def getSerialNumber(accessionNumber):
        with open("tests/links.csv", "r") as f:
            next(f, None)
            reader = csv.reader(f)
            for line in reader:
                if re.search(line[0], accessionNumber):
                    return line[1]

    def test_originalFilesHaveAttributes(self):
        self.assertEqual(self.orig.PatientName, "Anonymous Female 1959")
        self.assertEqual(self.orig.AccessionNumber, "R9BF8PC1GE")

    def test_accessionNumberIsReplaced(self):
        self.assertTrue(ACCESSION_NUMBER in self.pseu)
        self.assertEqual(self.pseu.AccessionNumber, self.sernum)

    def test_nonWhiteListedAndRequiredTagsAreCleaned(self):
        self.assertEqual(self.pseu.PatientID, "")
        self.assertEqual(self.pseu.StudyDate, "")

    def test_whiteListedAndRequiredTagsAreNotCleaned(self):
        self.assertEqual(self.pseu.PatientName, "Anonymous Female 1959")

    def test_nonWhiteListedTagsAreRemoved(self):
        self.assertFalse(STATION_NAME in self.pseu)

    def test_pixelDataAreNotRemoved(self):
        self.assertTrue(PIXEL_DATA in self.pseu)

    def test_whiteListedTagsAreNotRemoved(self):
        self.assertTrue(IMAGE_LATERALITY in self.pseu)
        self.assertTrue(self.pseu[IMAGE_LATERALITY].value is not None)
        self.assertTrue(self.pseu[IMAGE_LATERALITY].value.strip() != '')


if __name__ == '__main__':
    unittest.main()
