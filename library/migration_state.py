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
  diff_mode:
    support: full
    version_added: 8.3.0
options:
  name:
    description:
      - The name of the state key
    type: str
    default: keyname
  state:
    choices: ['completed', 'started']
    description:
      - State of given stage
    type: str
    default: started
  state_file:
    description:
      - The path to the downloaded state file
    type: path
    required: true
notes:
   - To just run a C(terraform plan), use check mode.
requirements: [ "terraform" ]
author: "Vadim romanov (@vadimr)"
'''

EXAMPLES = """
- name: Basic deploy of a service
  community.general.migration_state:
    project_path: '{{ project_dir }}'
    state: present

- name: Define the backend configuration at init
  community.general.terraform:
    project_path: 'project/'
    state: "{{ state }}"
    force_init: true
    backend_config:
      region: "eu-west-1"
      bucket: "some-bucket"
      key: "random.tfstate"

- name: Define the backend configuration with one or more files at init
  community.general.terraform:
    project_path: 'project/'
    state: "{{ state }}"
    force_init: true
    backend_config_files:
      - /path/to/backend_config_file_1
      - /path/to/backend_config_file_2

- name: Disable plugin discovery and auto-download by setting plugin_paths
  community.general.terraform:
    project_path: 'project/'
    state: "{{ state }}"
    force_init: true
    plugin_paths:
      - /path/to/plugins_dir_1
      - /path/to/plugins_dir_2

- name: Complex variables example
  community.general.terraform:
    project_path: '{{ project_dir }}'
    state: present
    complex_vars: true
    variables:
      vm_name: "{{ inventory_hostname }}"
      vm_vcpus: 2
      vm_mem: 2048
      vm_additional_disks:
        - label: "Third Disk"
          size: 40
          thin_provisioned: true
          unit_number: 2
        - label: "Fourth Disk"
          size: 22
          thin_provisioned: true
          unit_number: 3
    force_init: true

"""

RETURN = """
outputs:
  type: complex
  description: A dictionary of all the TF outputs by their assigned name. Use RV(ignore:outputs.MyOutputName.value) to access the value.
  returned: on success
  sample: '{"bukkit_arn": {"sensitive": false, "type": "string", "value": "arn:aws:s3:::tf-test-bukkit"}'
  contains:
    sensitive:
      type: bool
      returned: always
      description: Whether Terraform has marked this value as sensitive
    type:
      type: str
      returned: always
      description: The type of the value (string, int, etc)
    value:
      type: str
      returned: always
      description: The value of the output as interpolated by Terraform
stdout:
  type: str
  description: Full C(terraform) command stdout, in case you want to display it or examine the event log
  returned: always
  sample: ''
command:
  type: str
  description: Full C(terraform) command built by this module, in case you want to re-run the command outside the module or debug a problem.
  returned: always
  sample: terraform apply ...
"""

import os
import json
import tempfile
from ansible.module_utils.six.moves import shlex_quote
from ansible.module_utils.six import integer_types

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.community.general.plugins.module_utils.version import LooseVersion

module = None


def get_version(bin_path):
    extract_version = module.run_command([bin_path, 'version', '-json'])
    terraform_version = (json.loads(extract_version[1]))['terraform_version']
    return terraform_version


def preflight_validation(state_file):
    if state_file is None :
        module.fail_json(msg="Path for state file can not be None")
    # if not os.path.exists(bin_path):
    #     module.fail_json(msg="Path for Terraform binary '{0}' doesn't exist on this host - check the path and try again please.".format(bin_path))
    # if not os.path.isdir(project_path):
    #     module.fail_json(msg="Path for Terraform project '{0}' doesn't exist on this host - check the path and try again please.".format(project_path))
    # if LooseVersion(version) < LooseVersion('0.15.0'):
    #     module.run_command([bin_path, 'validate', '-no-color'] + variables_args, check_rc=True, cwd=project_path)
    # else:
    #     module.run_command([bin_path, 'validate', '-no-color'], check_rc=True, cwd=project_path)


def _state_args(state_file):
    if not state_file:
        return []
    if not os.path.exists(state_file):
        module.warn('Could not find state_file "{0}", the process will not destroy any resources, please check your state file path.'.format(state_file))
    return ['-state', state_file]

def get_state(state_file):
    if not state_file:
        return []
    if not os.path.exists(state_file):
        module.warn('Could not find state_file "{0}", the process will not destroy any resources, please check your state file path.'.format(state_file))
    return ['-state', state_file]

def init_plugins(bin_path, project_path, backend_config, backend_config_files, init_reconfigure, provider_upgrade, plugin_paths, workspace):
    command = [bin_path, 'init', '-input=false', '-no-color']
    if backend_config:
        for key, val in backend_config.items():
            command.extend([
                '-backend-config',
                '{0}={1}'.format(key, val)
            ])
    if backend_config_files:
        for f in backend_config_files:
            command.extend(['-backend-config', f])
    if init_reconfigure:
        command.extend(['-reconfigure'])
    if provider_upgrade:
        command.extend(['-upgrade'])
    if plugin_paths:
        for plugin_path in plugin_paths:
            command.extend(['-plugin-dir', plugin_path])
    rc, out, err = module.run_command(command, check_rc=True, cwd=project_path, environ_update={"TF_WORKSPACE": workspace})


def get_workspace_context(bin_path, project_path):
    workspace_ctx = {"current": "default", "all": []}
    command = [bin_path, 'workspace', 'list', '-no-color']
    rc, out, err = module.run_command(command, cwd=project_path)
    if rc != 0:
        module.warn("Failed to list Terraform workspaces:\n{0}".format(err))
    for item in out.split('\n'):
        stripped_item = item.strip()
        if not stripped_item:
            continue
        elif stripped_item.startswith('* '):
            workspace_ctx["current"] = stripped_item.replace('* ', '')
            workspace_ctx["all"].append(stripped_item.replace('* ', ''))
        else:
            workspace_ctx["all"].append(stripped_item)
    return workspace_ctx

def init_state_file():
    statefile_template = {"version":"v1", "stages":{"first": "completed", "second":"started"}}
    return statefile_template

def _create_state_file(state_file):
    with open(state_file, "w") as file:
      json.dump(init_state_file(), file)

def _delete_state_file(state_file):
    os.remove(state_file)

def _read_state_file(state_file):
    with open(state_file, "r") as file:
      state = json.loads(file.read())
      return state

def _write_state_file(state_file, state):
    with open(state_file, "w") as file:
      json.dump(state, file)

def _get_state_key(state_file, key_name):
    state = _read_state_file(state_file)
    return state["stages"][key_name]

def _set_state_key(state_file, key_name, value):
    state = _read_state_file(state_file)
    state["stages"][key_name]=value
    _write_state_file(state_file, state)
    return state

def _workspace_cmd(bin_path, project_path, action, workspace):
    command = [bin_path, 'workspace', action, workspace, '-no-color']
    rc, out, err = module.run_command(command, check_rc=True, cwd=project_path)
    return rc, out, err


def build_plan(command, project_path, variables_args, state_file, targets, state, args, plan_path=None):
    if plan_path is None:
        f, plan_path = tempfile.mkstemp(suffix='.tfplan')

    local_command = command[:]

    plan_command = [command[0], 'plan']

    if state == "planned":
        for c in local_command[1:]:
            plan_command.append(c)

    if state == "present":
        for a in args:
            local_command.remove(a)
        for c in local_command[1:]:
            plan_command.append(c)

    if state == "absent":
        for a in args:
            plan_command.append(a)

    plan_command.extend(['-input=false', '-no-color', '-detailed-exitcode', '-out', plan_path])

    for t in targets:
        plan_command.extend(['-target', t])

    plan_command.extend(_state_args(state_file))

    rc, out, err = module.run_command(plan_command + variables_args, cwd=project_path)

    if rc == 0:
        # no changes
        return plan_path, False, out, err, plan_command if state == 'planned' else command
    elif rc == 1:
        # failure to plan
        module.fail_json(
            msg='Terraform plan could not be created\nSTDOUT: {out}\nSTDERR: {err}\nCOMMAND: {cmd} {args}'.format(
                out=out,
                err=err,
                cmd=' '.join(plan_command),
                args=' '.join([shlex_quote(arg) for arg in variables_args])
            )
        )
    elif rc == 2:
        # changes, but successful
        return plan_path, True, out, err, plan_command if state == 'planned' else command

    module.fail_json(msg='Terraform plan failed with unexpected exit code {rc}.\nSTDOUT: {out}\nSTDERR: {err}\nCOMMAND: {cmd} {args}'.format(
        rc=rc,
        out=out,
        err=err,
        cmd=' '.join(plan_command),
        args=' '.join([shlex_quote(arg) for arg in variables_args])
    ))


def get_diff(diff_output):
    def get_tf_resource_address(e):
        return e['resource']

    diff_json_output = json.loads(diff_output)

    # Ignore diff if resource_changes does not exists in tfplan
    if 'resource_changes' in diff_json_output:
        tf_reosource_changes = diff_json_output['resource_changes']
    else:
        module.warn("Cannot find resource_changes in terraform plan, diff/check ignored")
        return False, {}

    diff_after = []
    diff_before = []
    changed = False
    for item in tf_reosource_changes:
        item_change = item['change']
        tf_before_state = {'resource': item['address'], 'change': item['change']['before']}
        tf_after_state = {'resource': item['address'], 'change': item['change']['after']}

        if item_change['actions'] == ['update'] or item_change['actions'] == ['delete', 'create']:
            diff_before.append(tf_before_state)
            diff_after.append(tf_after_state)
            changed = True

        if item_change['actions'] == ['delete']:
            diff_before.append(tf_before_state)
            changed = True

        if item_change['actions'] == ['create']:
            diff_after.append(tf_after_state)
            changed = True

    diff_before.sort(key=get_tf_resource_address)
    diff_after.sort(key=get_tf_resource_address)

    return changed, dict(
        before=({'data': diff_before}),
        after=({'data': diff_after}),
    )


def main():
    global module
    module = AnsibleModule(
        argument_spec=dict(
            name=dict(type='str', default='keyname'),

            state=dict(default='started', choices=['completed', 'started']),
            state_file=dict(type='path')
        ),
        supports_check_mode=True,
    )
    name = module.params.get('name')
    state = module.params.get('state')
    state_file = module.params.get('state_file')

    # if bin_path is not None:
    #     command = [bin_path]
    # else:
    #     command = [module.get_bin_path('terraform', required=True)]

    # checked_version = get_version(command[0])

    # if LooseVersion(checked_version) < LooseVersion('0.15.0'):
    #     DESTROY_ARGS = ('destroy', '-no-color', '-force')
    #     APPLY_ARGS = ('apply', '-no-color', '-input=false', '-auto-approve=true')
    # else:
    #     DESTROY_ARGS = ('destroy', '-no-color', '-auto-approve')
    #     APPLY_ARGS = ('apply', '-no-color', '-input=false', '-auto-approve')

    # if force_init:
    #     if overwrite_init or not os.path.isfile(os.path.join(project_path, ".terraform", "terraform.tfstate")):
    #         init_plugins(command[0], project_path, backend_config, backend_config_files, init_reconfigure, provider_upgrade, plugin_paths, workspace)

    # workspace_ctx = get_workspace_context(command[0], project_path)
    # if workspace_ctx["current"] != workspace:
    #     if workspace not in workspace_ctx["all"]:
    #         create_workspace(command[0], project_path, workspace)
    #     else:
    #         select_workspace(command[0], project_path, workspace)

    # if state == 'present':
    #     command.extend(APPLY_ARGS)
    # elif state == 'absent':
    #     command.extend(DESTROY_ARGS)

    # if state == 'present' and module.params.get('parallelism') is not None:
    #     command.append('-parallelism=%d' % module.params.get('parallelism'))

    def format_args(vars):
        if isinstance(vars, str):
            return '"{string}"'.format(string=vars.replace('\\', '\\\\').replace('"', '\\"')).replace('\n', '\\n')
        elif isinstance(vars, bool):
            if vars:
                return 'true'
            else:
                return 'false'
        return str(vars)

    

    # def process_complex_args(vars):
    #     ret_out = []
    #     if isinstance(vars, dict):
    #         for k, v in vars.items():
    #             if isinstance(v, dict):
    #                 ret_out.append('{0}={{{1}}}'.format(k, process_complex_args(v)))
    #             elif isinstance(v, list):
    #                 ret_out.append("{0}={1}".format(k, process_complex_args(v)))
    #             elif isinstance(v, (integer_types, float, str, bool)):
    #                 ret_out.append('{0}={1}'.format(k, format_args(v)))
    #             else:
    #                 # only to handle anything unforeseen
    #                 module.fail_json(msg="Supported types are, dictionaries, lists, strings, integer_types, boolean and float.")
    #     if isinstance(vars, list):
    #         l_out = []
    #         for item in vars:
    #             if isinstance(item, dict):
    #                 l_out.append("{{{0}}}".format(process_complex_args(item)))
    #             elif isinstance(item, list):
    #                 l_out.append("{0}".format(process_complex_args(item)))
    #             elif isinstance(item, (str, integer_types, float, bool)):
    #                 l_out.append(format_args(item))
    #             else:
    #                 # only to handle anything unforeseen
    #                 module.fail_json(msg="Supported types are, dictionaries, lists, strings, integer_types, boolean and float.")

    #         ret_out.append("[{0}]".format(",".join(l_out)))
    #     return ",".join(ret_out)

    # variables_args = []
    # if complex_vars:
    #     for k, v in variables.items():
    #         if isinstance(v, dict):
    #             variables_args.extend([
    #                 '-var',
    #                 '{0}={{{1}}}'.format(k, process_complex_args(v))
    #             ])
    #         elif isinstance(v, list):
    #             variables_args.extend([
    #                 '-var',
    #                 '{0}={1}'.format(k, process_complex_args(v))
    #             ])
    #         # on the top-level we need to pass just the python string with necessary
    #         # terraform string escape sequences
    #         elif isinstance(v, str):
    #             variables_args.extend([
    #                 '-var',
    #                 "{0}={1}".format(k, v)
    #             ])
    #         else:
    #             variables_args.extend([
    #                 '-var',
    #                 '{0}={1}'.format(k, format_args(v))
    #             ])
    # else:
    #     for k, v in variables.items():
    #         variables_args.extend([
    #             '-var',
    #             '{0}={1}'.format(k, v)
    #         ])

    # if variables_files:
    #     for f in variables_files:
    #         variables_args.extend(['-var-file', f])

    preflight_validation(state_file)
    _create_state_file(state_file)
    status = _get_state_key(state_file, name)
    if status != state:
        _set_state_key(state_file,name,state)

    

    # if state == 'started':

    # if module.params.get('lock') is not None:
    #     if module.params.get('lock'):
    #         command.append('-lock=true')
    #     else:
    #         command.append('-lock=false')
    # if module.params.get('lock_timeout') is not None:
    #     command.append('-lock-timeout=%ds' % module.params.get('lock_timeout'))

    # for t in (module.params.get('targets') or []):
    #     command.extend(['-target', t])

    # # we aren't sure if this plan will result in changes, so assume yes
    # needs_application, changed = True, False

    # out, err = '', ''

    # if state == 'absent':
    #     command.extend(variables_args)
    # elif state == 'present' and plan_file:
    #     if any([os.path.isfile(project_path + "/" + plan_file), os.path.isfile(plan_file)]):
    #         command.append(plan_file)
    #     else:
    #         module.fail_json(msg='Could not find plan_file "{0}", check the path and try again.'.format(plan_file))
    # else:
    #     plan_file, needs_application, out, err, command = build_plan(command, project_path, variables_args, state_file,
    #                                                                  module.params.get('targets'), state, APPLY_ARGS, plan_file)
    #     if state == 'present' and check_destroy and '- destroy' in out:
    #         module.fail_json(msg="Aborting command because it would destroy some resources. "
    #                              "Consider switching the 'check_destroy' to false to suppress this error")
    #     command.append(plan_file)

    # result_diff = dict()
    # if module._diff or module.check_mode:
    #     if state == 'absent':
    #         plan_absent_args = ['-destroy']
    #         plan_file, needs_application, out, err, command = build_plan(command, project_path, variables_args, state_file,
    #                                                                      module.params.get('targets'), state, plan_absent_args, plan_file)
    #     diff_command = [command[0], 'show', '-json', plan_file]
    #     rc, diff_output, err = module.run_command(diff_command, check_rc=False, cwd=project_path)
    #     changed, result_diff = get_diff(diff_output)
    #     if rc != 0:
    #         if workspace_ctx["current"] != workspace:
    #             select_workspace(command[0], project_path, workspace_ctx["current"])
    #         module.fail_json(msg=err.rstrip(), rc=rc, stdout=out,
    #                          stdout_lines=out.splitlines(), stderr=err,
    #                          stderr_lines=err.splitlines(),
    #                          cmd=' '.join(command))

    # if needs_application and not module.check_mode and state != 'planned':
    #     rc, out, err = module.run_command(command, check_rc=False, cwd=project_path)
    #     if rc != 0:
    #         if workspace_ctx["current"] != workspace:
    #             select_workspace(command[0], project_path, workspace_ctx["current"])
    #         module.fail_json(msg=err.rstrip(), rc=rc, stdout=out,
    #                          stdout_lines=out.splitlines(), stderr=err,
    #                          stderr_lines=err.splitlines(),
    #                          cmd=' '.join(command))
    #     # checks out to decide if changes were made during execution
    #     if ' 0 added, 0 changed' not in out and not state == "absent" or ' 0 destroyed' not in out:
    #         changed = True

    # outputs_command = [command[0], 'output', '-no-color', '-json'] + _state_args(state_file)
    # rc, outputs_text, outputs_err = module.run_command(outputs_command, cwd=project_path)
    # outputs = {}
    # if rc == 1:
    #     module.warn("Could not get Terraform outputs. This usually means none have been defined.\nstdout: {0}\nstderr: {1}".format(outputs_text, outputs_err))
    # elif rc != 0:
    #     module.fail_json(
    #         msg="Failure when getting Terraform outputs. "
    #             "Exited {0}.\nstdout: {1}\nstderr: {2}".format(rc, outputs_text, outputs_err),
    #         command=' '.join(outputs_command))
    # else:
    #     outputs = json.loads(outputs_text)

    # # Restore the Terraform workspace found when running the module
    # if workspace_ctx["current"] != workspace:
    #     select_workspace(command[0], project_path, workspace_ctx["current"])
    # if state == 'absent' and workspace != 'default' and purge_workspace is True:
    #     remove_workspace(command[0], project_path, workspace)

    result = {
        'name': name,
        'state': status,
        # 'workspace': workspace,
        # 'outputs': outputs,
        # 'stdout': out,
        # 'stderr': err,
        # 'command': ' '.join(command),
        # 'changed': changed,
        # 'diff': result_diff,
    }

    module.exit_json(**result)


if __name__ == '__main__':
    main()
