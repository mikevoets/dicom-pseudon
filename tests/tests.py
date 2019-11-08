import unittest
import pydicom
import dicom_pseudon

STUDY_ID = (0x20, 0x10)
ACCESSION_NUMBER = (0x8, 0x50)
IMAGE_LATERALITY = (0x20, 0x62)
PATIENT_ORIENTATION = (0x20, 0x20)


class TestDicomPseudon(unittest.TestCase):

    def setUp(self):
        pass

    def test(self):
        ds = pydicom.read_file("tests/samples/1/1_lbm/1.dcm")
        self.assertEqual(ds.PatientName, "Anonymous Female 1959")
        self.assertEqual(ds.AccessionNumber, "R9BF8PC1GE")
        dp = dicom_pseudon.DicomPseudon("tests/white_list.json",
                                        quarantine="tests/quarantine", index_file="tests/index.db",
                                        modalities=["mg"], log_file=None)
        dp.create_index("tests/samples", "tests/links.csv", " ", True)
        dp.run("tests/samples", "tests/clean")
        ds = pydicom.read_file("tests/clean/12345/1.dcm")
        self.assertTrue(ds.PatientName, "Anonymous Female 1959")
        self.assertTrue(ACCESSION_NUMBER in ds)
        self.assertEqual(ds.AccessionNumber, "12345")
        self.assertTrue(IMAGE_LATERALITY in ds)
        self.assertTrue(ds.ImageLaterality in ["R", "L"])
        # All non-whitelisted tags should be gone
        self.assertFalse(STUDY_ID in ds)
        self.assertFalse(PATIENT_ORIENTATION in ds)

if __name__ == '__main__':
    unittest.main()
