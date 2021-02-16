#!/bin/bash

module_path="${1}"

echo Creating environment in ${module_path}

# Create the environment.

python3 -m venv ${module_path}/venv
source ${module_path}/venv/bin/activate
# Install requirements
pip install -r requirements_test.txt

while [ ! -f ${module_path}/setup.py ]; do sleep 1; done
chmod a+x ${module_path}/setup.py

echo Python environment created in ${module_path} and requirements installed.
