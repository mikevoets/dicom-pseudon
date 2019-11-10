#!/bin/bash
PYTHONPATH=PYTHONPATH:`pwd` coverage run --include=./dicom_pseudon.py tests/tests.py
