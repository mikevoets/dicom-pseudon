# Dicom Pseudon - Python DICOM Pseudonymizer
# Copyright (c) 2019  Mike Voets
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
import secrets
import re
import os
import shutil


STUDY_ID = (0x20, 0x10)
ACCESSION_NUMBER = (0x8, 0x50)
IMAGE_LATERALITY = (0x20, 0x62)
PATIENT_ORIENTATION = (0x20, 0x20)


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

        with open("tests/links.csv", "w") as f:
            writer = csv.writer(f)
            writer.writerow(['Invitasjonsnummer', 'Loepenummer'])

            for ds, path in walk_dicoms("tests/samples"):
                acc = ds.AccessionNumber

                if acc in acc_set:
                    continue

                acc_set.add(acc)
                start = random.randint(0, int(len(acc)/4))
                end = -random.randint(1, int(len(acc)/4))
                serial_num = secrets.token_hex(10)
                writer.writerow([acc[start:end], serial_num])

    def tearDown(self):
        if os.path.isfile("tests/links.csv"):
            os.remove("tests/links.csv")
        if os.path.isfile("tests/index.db"):
            os.remove("tests/index.db")
        if os.path.exists("tests/clean"):
            shutil.rmtree("tests/clean")
        if os.path.exists("tests/quarantine"):
            shutil.rmtree("tests/quarantine")

    def test(self):
        ds = pydicom.read_file("tests/samples/1/1_lbm/1.dcm")
        self.assertEqual(ds.PatientName, "Anonymous Female 1959")
        self.assertEqual(ds.AccessionNumber, "R9BF8PC1GE")
        dp = dicom_pseudon.DicomPseudon("tests/white_list.json",
                                        quarantine="tests/quarantine", index_file="tests/index.db",
                                        modalities=["mg"], log_file=None)
        dp.create_index("tests/samples", "tests/links.csv", skip_first_line=True)
        dp.run("tests/samples", "tests/clean")

        serial_num = None
        with open("tests/links.csv", "r") as f:
            next(f, None)
            reader = csv.reader(f)
            for line in reader:
                if re.search(line[0], ds.AccessionNumber):  # R9BF8PC1GE
                    serial_num = line[1]
                    break

        self.assertTrue(serial_num is not None)
        ds = pydicom.read_file("tests/clean/%s/1.dcm" % serial_num)
        self.assertTrue(ds.PatientName, "Anonymous Female 1959")
        self.assertTrue(ACCESSION_NUMBER in ds)
        self.assertEqual(ds.AccessionNumber, serial_num)
        # All non-whitelisted tags should be gone
        self.assertFalse(IMAGE_LATERALITY in ds)
        # self.assertFalse(STUDY_ID in ds)
        # self.assertFalse(PATIENT_ORIENTATION in ds)


if __name__ == '__main__':
    unittest.main()
