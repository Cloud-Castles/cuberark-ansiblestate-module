#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2024, Vadim romanov <vadim.romanov@cyberark.com>
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type


DOCUMENTATION = r'''
---
module: migration_state
short_description: 
  - Manages an arbitrary state file used to mark workflow tasks and blocks 
    as completed for skipping during reruns of migration
description:
  - This module is used to set arbitrarily named state keys and check them 
    during ansible runs before tasks/blocks, in order to skip them when required
    In some cases idemnpotency cannot be achieved during umu or h2p migrations,
    and it is simpler to just manually mark the tasks completed and skip them
    during reruns by referencing the state
extends_documentation_fragment:
  - community.general.attributes
attributes:
  check_mode:
    support: full
options:
  name:
    description:
      - The name of the state key
    type: str
    default: keyname
    required: true
  state:
    choices: ['completed', 'started']
    description:
      - State of given stage
    type: str
    default: started
    required: false
  state_file:
    description:
      - The path to the downloaded state file
    type: path
    required: true
  state_backend:
    description:
      - The type of backend to use for state storage
    type: str
    choices: ['local', 's3']
    required: false
    default: local
  state_bucket_name:
    description:
      - The name of the bucket to use for state file storage
    required: false
    default: ca-state-bucket
    type: str
  read_state:
    description:
      - If true, will get the value of the key in the name argument without changing it
    type: bool
    required: false
    default: false

requirements: [ "boto3" ]
author: "Vadim romanov (@vadimr)"
'''

EXAMPLES = """  
- name: init local state file
  migration_state:
    name: 'initialized'
    state_file: 'state.json'
    state_backend: 'local'
    state: 'completed'
    
- name: init remote s3 state file
  migration_state:
    name: 'initialized'
    state_file: 'path/to/state.json'
    state_backend: 's3'
    state_bucket_name: 'ca-state-bucket'
    state: 'completed'

"""

RETURN = """
name:
  type: str
  description: The name of the step
  returned: always
  sample: validate-users-step
state:
  type: str
  description: The final state of the stage.
  returned: always
  sample: completed
"""

import os
import json
import tempfile
import boto3

from ansible.module_utils.six.moves import shlex_quote
from ansible.module_utils.six import integer_types

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.community.general.plugins.module_utils.version import LooseVersion

module = None

state_schema_version = "v1"
state_template = {"version":state_schema_version, "stages": {}}

def get_version(bin_path):
    extract_version = module.run_command([bin_path, 'version', '-json'])
    terraform_version = (json.loads(extract_version[1]))['terraform_version']
    return terraform_version


def preflight_validation(state_file):
    if state_file is None :
        module.fail_json(msg="Path for state file can not be None")

def init_state_file(state_file, state_backend='local',bucket_name=None):
    if state_backend == 'local':
      if not os.path.exists(state_file):
          return _create_local_state_file(state_file)
    elif state_backend == 's3':
        return _create_s3_state_file(bucket_name,state_file)
        
    else:
        return None

def _create_local_state_file(state_file):
    with open(state_file, "w") as file:
      json.dump(state_template, file)
    return state_file

def _create_s3_state_file(bucket_name, state_file):
    client = boto3.client('s3')
    response = client.put_object(
    Body=json.dumps(state_template),
    Bucket=bucket_name,
    Key=state_file,
    )
    return "{}/{}".format(bucket_name,state_file)

def _read_state_file(state_file, state_backend='local',bucket_name=None):
    if state_backend == 'local':
        with open(state_file, "r") as file:
          state = json.loads(file.read())
          return state
    elif state_backend == 's3':
        client = boto3.client('s3')
        response = client.get_object(
        Bucket=bucket_name,
        Key=state_file,
        )
        state = json.loads(response['Body'].read().decode('utf-8'))
        return state      
    else:
        return None

def _write_state_file(state, state_file, state_backend='local', bucket_name=None):
    if state_backend == 'local':
        with open(state_file, "w") as file:
          json.dump(state, file)
    elif state_backend == 's3':
        client = boto3.client('s3')
        response = client.put_object(
        Body=json.dumps(state),
        Bucket=bucket_name,
        Key=state_file,
        )
        return response      
    else:
        return None

def _get_state_key(state_file, key_name, state_backend='local',bucket_name=None):
    state = _read_state_file(state_file, state_backend, bucket_name)
    val = state.get('stages', {}).get(key_name, None)
    return val

def _set_state_key(state_file, key_name, value, state_backend='local',bucket_name=None):
        state = _read_state_file(state_file, state_backend, bucket_name)
        state["stages"][key_name]=value
        _write_state_file(state, state_file, state_backend, bucket_name)
        return state      

def main():
    global module
    module = AnsibleModule(
        argument_spec=dict(
            name=dict(type='str', default='keyname'),
            state_backend=dict(type='str',default='local'),
            state_bucket_name=dict(type='str',default='ca-state-bucket'),
            state=dict(default='started', choices=['completed', 'started']),
            state_file=dict(type='path'),
            read_state=dict(type='bool',default=False,)
        ),
        supports_check_mode=True,
    )
    name = module.params.get('name')
    state = module.params.get('state')
    state_file = module.params.get('state_file')
    state_backend = module.params.get('state_backend')
    state_bucket_name = module.params.get('state_bucket_name')
    read_state = module.params.get('read_state')
    
    changed = False
    final_state = 'started'

    init_state_file(state_file, state_backend, state_bucket_name)


    preflight_validation(state_file)
    state_file_state = _get_state_key(state_file, name, state_backend, state_bucket_name)
    if read_state:
        changed = False
        final_state = state_file_state
    elif state_file_state == 'completed':
        changed = False
        final_state = 'completed'
    elif state_file_state != state:
        _set_state_key(state_file,name,state, state_backend, state_bucket_name)
        changed = True
        final_state = state

    result = {
        'name': name,
        'state': final_state,
        # 'workspace': workspace,
        # 'outputs': outputs,
        # 'stdout': out,
        # 'stderr': err,
        # 'command': ' '.join(command),
        'changed': changed
        # 'diff': result_diff,
    }

    module.exit_json(**result)


if __name__ == '__main__':
    main()
