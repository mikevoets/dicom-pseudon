Copyright (c) 2019, Mike Voets. All rights reserved.

# Python DICOM Pseudonymizer

This is a Python script for pseudonymization of DICOM files, and can be used from the command line. It takes a source csv file with variables and a source directory containing DICOM files, anonymizes them, and places them to the specified destination. The source csv file will also be de-identified and written to the specific destination. The anonymized DICOM files are also renamed in order to not remain having any sensitive information.

The CSV links file should contain variables in a specific order. It is assumed that each row has two numbers: the invitation number and serial number, on the 1st and 2nd place in each row, respectively. The invitation number will be matched with the value in the "(0008, 0050) Accession Number" tag in the corresponding DICOM file. Upon pseudonymization the value of the matching Accession Number will be replaced with the serial number from the CSV file.

## Prerequisites

The script runs with Python 3.6. See the [requirements](requirements.txt) for what third-party requirements you will need to have installed.

You can install all requirements by using pip:

```
pip install -U -r requirements.txt
```

Notice: For Windows users, it may be that cloning only works if you are using [Git bash](https://git-scm.com/downloads).

To test if the program runs correctly on your machine, run:

```
bash run_tests.sh
```

## Example

Assume the identified DICOM files are in a directory called `identified` in your home directory, and you want the pseudonymized files to be placed in a directory called `cleaned` in your home directory.

The CSV file that contains the mapping between invitation numbers and serial numbers is called `links.csv`.

The pseudonymization script creates a SQLite database to index the CSV file with the mapping. This file can be removed after running this script.

The white list JSON file that lists the tags that explicitly should not be removed by the pseudonymization script is called `white_list.json`.

Files that could not be linked according to the CSV input file; files are explicitly marked as containing burnt-in data; files that have a series description of "Patient Protocol"; files with a suspect manufacturer (North American Imaging or PACSGEAR); files that have an invalid modality, will be copied to the `quarantine` folder.

```
python dicom_pseudon.py identified cleaned links.csv white_list.json
```

As a default only [modalities](https://www.dicomlibrary.com/dicom/modality/) MR and CT are allowed. If for any reason you need to specify other modalities, you will need to use the `--modalities` argument and specify the allowed modalities yourself. Multiple modalities should be comma-separated.


## License

This project is licensed under the GNU GPL v3.0 License - see the [LICENSE.md](LICENSE.md) file for details.
