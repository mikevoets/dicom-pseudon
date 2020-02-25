# Python DICOM Pseudonymizer

[![Build Status](https://travis-ci.org/mikevoets/dicom-pseudon.svg?branch=master)](https://travis-ci.org/mikevoets/dicom-pseudon) [![Coverage Status](https://coveralls.io/repos/github/mikevoets/dicom-pseudon/badge.svg?branch=master)](https://coveralls.io/github/mikevoets/dicom-pseudon?branch=master) [![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE.md)

This is a Python script for pseudonymization of DICOM files, and can be used from the command line. It takes a CSV file with variables and a source directory containing DICOM files, along with a CSV white list with tags that should not be removed. The script then pseudonymizes the DICOM files in the source directory, and places them to the specified destination.

The CSV file with the variables should contain variables in a specific order. It is assumed that each row has two numbers: the invitation number and serial number, on the 1st and 2nd place in each row, respectively. The invitation number will be matched with the value in the "(0008, 0050) Accession Number" tag in the corresponding DICOM file. During pseudonymization the value of the matching Accession Number will be replaced with the serial number from the CSV file.

The CSV file with the white list should contain a list of tags that should not be removed from the DICOM files. It is assumed this file has only one column for the tags, and that every row contains one unique tag. The tag group and tag element numbers should be separated by a comma. White-spaces and parenthesizes are ignored. Examples of accepted tags are "0020, 0062", "0020,0062", "(0020, 0062)", etc.

## Prerequisites

The script runs with Python 3.8. See the [requirements](requirements.txt) for what third-party requirements you will need to have installed.

You can install all requirements by using pip:

```
pip install -U -r requirements.txt
```

Notice: For Windows users, it may be that cloning only works if you are using [Git bash](https://git-scm.com/downloads).

## Example

Assume the identified DICOM files are in a directory called `identified` in your home directory, and you want the pseudonymized files to be placed in a directory called `cleaned` in your home directory.

The CSV file that contains the mapping between invitation numbers and serial numbers is called `links.csv`.

The pseudonymization script creates a SQLite database to index the CSV file with the mapping from the links file. This file can be removed after running this script.

The white list CSV file that lists the tags that explicitly should not be removed by the pseudonymization script is called `white_list.csv`.

Files that could not be linked according to the CSV input file; files that are explicitly marked as containing burnt-in data; files that have a series description of "Patient Protocol"; files with a suspect manufacturer (North American Imaging or PACSGEAR); files that have an invalid modality, will be copied to the `quarantine` folder.

```
python dicom_pseudon.py identified cleaned links.csv white_list.csv
```

As a default only [modalities](https://www.dicomlibrary.com/dicom/modality/) MR and CT are allowed. If for any reason you need to specify other modalities, you will need to use the `--modalities` argument and specify the allowed modalities yourself. Multiple modalities should be comma-separated.

Run the script with the `-h` flag to see all accepted script parameters.

## Validation

To validate that all DICOM tags except the ones specified in the white list are removed, run the following script:

```
python validate_dicom_pseudon.py cleaned white_list.csv
```

Run the script with the `-h` flag to see all accepted script parameters.

The following links specify tags that are required in DICOM files, and they are excluded from removal during pseudonymization:

https://github.com/dicom/ruby-dicom

https://www.pclviewer.com/help/required_dicom_tags.htm

http://dicom.nema.org/medical/Dicom/2016a/output/chtml/part03/sect_C.7.6.3.html

The pseudonymization script also adds the "(0012,0062) Patient Identity Removed" and "(0012,0063) Deidentification Method" to each DICOM file.

## License

Copyright (c) 2020  Mike Voets

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the [LICENSE.md](LICENSE.md) file for details.
