#
# Katello System actions
# Copyright (c) 2012 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public License,
# version 2 (GPLv2). There is NO WARRANTY for this software, express or
# implied, including the implied warranties of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. You should have received a copy of GPLv2
# along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.
#
# Red Hat trademarks are not licensed under GPLv2. No permission is
# granted to use or replicate Red Hat trademarks that are incorporated
# in this software or its documentation.
#

import os

from katello.client import constants
from katello.client.api.system import SystemAPI
from katello.client.api.task_status import SystemTaskStatusAPI
from katello.client.api.system_group import SystemGroupAPI
from katello.client.api.custom_info import CustomInfoAPI
from katello.client.api.utils import get_environment, get_system
from katello.client.cli.base import opt_parser_add_org, opt_parser_add_environment
from katello.client.core.base import BaseAction, Command
from katello.client.core.utils import test_record, convert_to_mime_type, attachment_file_name, save_report, \
    update_dict_unless_none
from katello.client.utils.printer import VerboseStrategy
from katello.client.core.utils import run_spinner_in_bg, wait_for_async_task, SystemAsyncTask, format_date
from katello.client.utils.encoding import u_str
from katello.client.utils import printer
from katello.client.server import ServerRequestError


# base system action --------------------------------------------------------

class SystemAction(BaseAction):

    def __init__(self):
        super(SystemAction, self).__init__()
        self.api = SystemAPI()


# system actions ------------------------------------------------------------

class List(SystemAction):

    description = _('list systems within an organization')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        opt_parser_add_environment(parser)
        parser.add_option('--pool', dest='pool_id',
                       help=_("pool ID to filter systems by subscriptions"))

    def check_options(self, validator):
        validator.require('org')

    def get_systems(self, org_name, env_name, pool_id):
        query = {'pool_id': pool_id} if pool_id else {}
        if env_name is None:
            return self.api.systems_by_org(org_name, query)
        else:
            environment = get_environment(org_name, env_name)
            return self.api.systems_by_env(environment["id"], query)

    def run(self):
        org_name = self.get_option('org')
        env_name = self.get_option('environment')
        pool_id = self.get_option('pool_id')

        systems = self.get_systems(org_name, env_name, pool_id)

        if env_name is None:
            self.printer.set_header(_("Systems List For Org [ %s ]") % org_name)
        else:
            self.printer.set_header(_("Systems List For Environment [ %s ] in Org [ %s ]") % (env_name, org_name))

        self.printer.add_column('name')
        self.printer.add_column('uuid')
        self.printer.add_column('ipv4_address')
        self.printer.add_column('serviceLevel', _('Service Level'))

        self.printer.print_items(systems)
        return os.EX_OK

class Info(SystemAction):

    description = _('display a system within an organization')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        parser.add_option('--name', dest='name',
                       help=_("system name (required)"))
        parser.add_option('--uuid', dest='uuid',
                       help=constants.OPT_HELP_SYSTEM_UUID)
        opt_parser_add_environment(parser)

    def check_options(self, validator):
        validator.require('org')
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')
        validator.mutually_exclude('environment', 'uuid')

    def run(self):
        org_name = self.get_option('org')
        env_name = self.get_option('environment')
        sys_name = self.get_option('name')
        sys_uuid = self.get_option('uuid')

        if sys_uuid:
            self.printer.set_header(_("System Information [ %s ]") % sys_uuid)
        elif env_name is None:
            self.printer.set_header(_("System Information For Org [ %s ]") % org_name)
        else:
            self.printer.set_header(_("System Information For Environment [ %s ] in Org [ %s ]") % (env_name, org_name))

        # get system details
        system = get_system(org_name, sys_name, env_name, sys_uuid)

        custom_info_api = CustomInfoAPI()
        custom_info = custom_info_api.get_custom_info("system", system['id'])
        system['custom_info'] = "[ %s ]" % ", ".join(["%s: [ %s ]" % (k, ", ".join(custom_info[k])) for k in custom_info.keys()])

        system["activation_keys"] = "[ "+ ", ".join([ak["name"] for ak in system["activation_key"]]) +" ]"
        if 'host' in system:
            system['host'] = system['host']['name']
        if 'guests' in system:
            system["guests"] = "[ "+ ", ".join([guest["name"] for guest in system["guests"]]) +" ]"

        self.printer.add_column('name')
        self.printer.add_column('ipv4_address')
        self.printer.add_column('uuid')
        self.printer.add_column('location')
        self.printer.add_column('created_at', 'Registered', formatter=format_date)
        self.printer.add_column('updated_at', 'Last updated', formatter=format_date)
        self.printer.add_column('description', multiline=True)
        if 'releaseVer' in system and system['releaseVer']:
            self.printer.add_column('releaseVer', 'OS release')
        self.printer.add_column('activation_keys', multiline=True, show_with=printer.VerboseStrategy)
        self.printer.add_column('host', show_with=printer.VerboseStrategy)
        self.printer.add_column('serviceLevel', _('Service Level'))
        self.printer.add_column('guests', show_with=printer.VerboseStrategy)
        if "template" in system:
            self.printer.add_column('template', show_with=printer.VerboseStrategy, value=system["template"]["name"])
        self.printer.add_column('custom_info', multiline=True, show_with=printer.VerboseStrategy)

        self.printer.print_item(system)

        return os.EX_OK

class InstalledPackages(SystemAction):

    description = _('display and manipulate with the installed packages of a system')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        parser.add_option('--name', dest='name',
            help=_("system name (required)"))
        parser.add_option('--uuid', dest='uuid',
                help=constants.OPT_HELP_SYSTEM_UUID)
        opt_parser_add_environment(parser)
        parser.add_option('--install', dest='install', type="list",
            help=_("packages to be installed remotely on the system, package names are separated with comma"))
        parser.add_option('--remove', dest='remove', type="list",
            help=_("packages to be removed remotely from the system, package names are separated with comma"))
        parser.add_option('--update', dest='update', type="list",
            help=_("packages to be updated on the system, use --all to update all packages," +
                " package names are separated with comma"))
        parser.add_option('--install_groups', dest='install_groups', type="list",
            help=_("package groups to be installed remotely on the system, group names are separated with comma"))
        parser.add_option('--remove_groups', dest='remove_groups', type="list",
            help=_("package groups to be removed remotely from the system, group names are separated with comma"))

    def check_options(self, validator):
        validator.require('org')
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')
        validator.mutually_exclude('environment', 'uuid')

        remote_actions = ('install', 'remove', 'update', 'install_groups', 'remove_groups')
        validator.require_at_most_one_of(remote_actions,
            message=_('You can specify at most one install/remove/update action per call'))


    def run(self):
        org_name = self.get_option('org')
        env_name = self.get_option('environment')
        sys_name = self.get_option('name')
        sys_uuid = self.get_option('uuid')

        install = self.get_option('install')
        remove = self.get_option('remove')
        update = self.get_option('update')
        install_groups = self.get_option('install_groups')
        remove_groups = self.get_option('remove_groups')

        task = None

        if env_name is None:
            self.printer.set_header(_("Package Information for System [ %s ] in Org [ %s ]") % (sys_name, org_name))
        else:
            self.printer.set_header(_("Package Information for System [ %s ] in Environment [ %s ] in Org [ %s ]") %
                (sys_name, env_name, org_name))

        system = get_system(org_name, sys_name, env_name, sys_uuid)
        system_id = system['uuid']

        if install:
            task = self.api.install_packages(system_id, install)
        if remove:
            task = self.api.remove_packages(system_id, remove)
        if update:
            if update == '--all':
                update_packages = []
            else:
                update_packages = update
            task = self.api.update_packages(system_id, update_packages)
        if install_groups:
            task = self.api.install_package_groups(system_id, install_groups)
        if remove_groups:
            task = self.api.remove_package_groups(system_id, remove_groups)

        if task:
            uuid = task["uuid"]
            print (_("Performing remote action [ %s ]... ") % uuid)
            task = SystemAsyncTask(task)
            run_spinner_in_bg(wait_for_async_task, [task])
            if task.succeeded():
                print _("Remote action finished:")
                print task.get_result_description()
                return os.EX_OK
            else:
                print _("Remote action failed:")
                print task.get_result_description()
                return os.EX_DATAERR

        packages = self.api.packages(system_id)

        self.printer.add_column('name', show_with=printer.VerboseStrategy)
        self.printer.add_column('vendor', show_with=printer.VerboseStrategy)
        self.printer.add_column('version', show_with=printer.VerboseStrategy)
        self.printer.add_column('release', show_with=printer.VerboseStrategy)
        self.printer.add_column('arch', show_with=printer.VerboseStrategy)
        self.printer.add_column('name_version_release_arch',
                    show_with=printer.GrepStrategy,
                    item_formatter=lambda p: "%s-%s-%s.%s" % (p['name'], p['version'], p['release'], p['arch']))

        self.printer.print_items(packages)

        return os.EX_OK


class TasksList(SystemAction):

    description = _('display status of remote tasks')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        parser.add_option('--name', dest='name',
                       help=_("system name"))
        parser.add_option('--uuid', dest='uuid',
                       help=constants.OPT_HELP_SYSTEM_UUID)
        opt_parser_add_environment(parser)

    def check_options(self, validator):
        validator.require('org')
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')
        validator.mutually_exclude('environment', 'uuid')

    def run(self):
        org_name = self.get_option('org')
        env_name = self.get_option('environment')
        sys_name = self.get_option('name')
        sys_uuid = self.get_option('uuid')

        self.printer.set_header(_("Remote tasks"))

        environment = get_environment(org_name, env_name)
        tasks = self.api.tasks(org_name, environment["id"], sys_name, sys_uuid)

        for t in tasks:
            t['result'] = "\n" + t['result_description']

        self.printer.add_column('uuid', name=_("Task ID"))
        self.printer.add_column('system_name', name=_("System"))
        self.printer.add_column('description', name=_("Action"))
        self.printer.add_column('created_at', name=_("Started"),
            formatter=format_date, show_with=printer.VerboseStrategy)
        self.printer.add_column('finish_time', name=_("Finished"), formatter=format_date,
            show_with=printer.VerboseStrategy)
        self.printer.add_column('state', name=_("Status"))
        self.printer.add_column('result', name=_("Result"), show_with=printer.VerboseStrategy)

        self.printer.print_items(tasks)

        return os.EX_OK

class TaskInfo(SystemAction):

    description = _('display status of remote task')

    def setup_parser(self, parser):
        parser.add_option('--id', dest='id',
                       help=_("UUID of the task"))

    def check_options(self, validator):
        validator.require('id')

    def run(self):
        uuid = self.get_option('id')

        self.printer.set_header(_("Remote task"))

        task = SystemTaskStatusAPI().status(uuid)
        task['result'] = "\n" + task['result_description']

        self.printer.add_column('system_name', name=_("System"))
        self.printer.add_column('description', name=_("Action"))
        self.printer.add_column('created_at', name=_("Started"), formatter=format_date)
        self.printer.add_column('finish_time', name=_("Finished"), formatter=format_date)
        self.printer.add_column('state', name=_("Status"))
        self.printer.add_column('result', name=_("Result"))
        self.printer.print_item(task)

        return os.EX_OK


class Releases(SystemAction):

    description = _('list releases available for the system')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        parser.add_option('--name', dest='name',
                       help=_("system name (if not specified, list all releases in the environment)"))
        parser.add_option('--uuid', dest='uuid',
                       help=constants.OPT_HELP_SYSTEM_UUID)
        opt_parser_add_environment(parser)

    def check_options(self, validator):
        validator.require('org')
        validator.require_one_of(('name', 'uuid', 'environment'))
        validator.mutually_exclude('name', 'uuid')
        validator.mutually_exclude('environment', 'uuid')

    def run(self):
        org_name = self.get_option('org')
        env_name = self.get_option('environment')
        sys_name = self.get_option('name')
        sys_uuid = self.get_option('uuid')

        if sys_uuid:
            releases = self.api.releases_for_system(sys_uuid)["releases"]
        if sys_name:
            system = get_system(org_name, sys_name)
            releases = self.api.releases_for_system(system["uuid"])["releases"]
        else:
            environment = get_environment(org_name, env_name)
            releases = self.api.releases_for_environment(environment['id'])["releases"]

        releases = [{"value": r} for r in releases]

        self.printer.set_header(_("Available releases"))
        self.printer.add_column('value')

        self.printer.print_items(releases)
        return os.EX_OK

class Facts(SystemAction):

    description = _('display a the hardware facts of a system')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        parser.add_option('--name', dest='name',
                       help=_("system name (required)"))
        parser.add_option('--uuid', dest='uuid',
                       help=constants.OPT_HELP_SYSTEM_UUID)
        opt_parser_add_environment(parser)

    def check_options(self, validator):
        validator.require('org')
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')
        validator.mutually_exclude('environment', 'uuid')

    def run(self):
        org_name = self.get_option('org')
        env_name = self.get_option('environment')
        sys_name = self.get_option('name')
        sys_uuid = self.get_option('uuid')

        if env_name is None:
            self.printer.set_header(_("System Facts For System [ %s ] in Org [ %s ]") %
                (sys_name if sys_name else sys_uuid, org_name))
        else:
            self.printer.set_header(_("System Facts For System [ %s ] in Environment [ %s ] in Org [ %s ]") %
                (sys_name, env_name, org_name))

        system = get_system(org_name, sys_name, env_name, sys_uuid)

        facts_hash = system['facts']
        facts_tuples_sorted = [(k, facts_hash[k]) for k in sorted(facts_hash.keys())]
        for k, v in facts_tuples_sorted:
            self.printer.add_column(k)
            system[k] = v

        self.printer.print_item(system)

        return os.EX_OK

class Register(SystemAction):

    description = _('register a system')

    def setup_parser(self, parser):
        parser.add_option('--name', dest='name', help=_("system name (required)"))
        opt_parser_add_org(parser, required=1)
        opt_parser_add_environment(parser)
        parser.add_option('--servicelevel', dest='sla', help=_("service level agreement"))
        parser.add_option('--activationkey', dest='activationkey',
            help=_("activation key, more keys are separated with comma e.g. --activationkey=key1,key2"))
        parser.add_option('--release', dest='release', help=_("values of $releasever for the system"))
        parser.add_option('--fact', dest='fact', action='append', nargs=2, metavar="KEY VALUE",
                               help=_("system facts"))

    def check_options(self, validator):
        validator.require(('name', 'org'))
        validator.require_at_most_one_of(('activationkey', 'environment'))

    def run(self):
        name = self.get_option('name')
        org = self.get_option('org')
        environment_name = self.get_option('environment')
        activation_keys = self.get_option('activationkey')
        release = self.get_option('release')
        sla = self.get_option('sla')
        facts = dict(self.get_option('fact') or {})

        environment_id = None
        if environment_name is not None:
            environment_id = get_environment(org, environment_name)['id']
        system = self.api.register(name, org, environment_id, activation_keys, 'system', release, sla, facts=facts)

        test_record(system,
            _("Successfully registered system [ %s ]") % name,
            _("Could not register system [ %s ]") % name
        )

class RemoveDeletion(SystemAction):

    description = _("remove a deletion record for hypervisor")

    def setup_parser(self, parser):
        parser.add_option("--uuid", dest="uuid",
                       help=_("hypervisor uuid (required"))

    def check_options(self, validator):
        validator.require_option('uuid')

    def run(self):
        uuid = self.get_option('uuid')
        self.api.remove_consumer_deletion_record(uuid)
        print _("Successfully removed deletion record for hypervisor with uuid [ %s ]") % uuid
        return os.EX_OK


class Unregister(SystemAction):

    description = _('unregister a system')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        parser.add_option('--name', dest='name',
                               help=_("system name (required)"))
        parser.add_option('--uuid', dest='uuid',
                               help=constants.OPT_HELP_SYSTEM_UUID)
        opt_parser_add_environment(parser)

    def check_options(self, validator):
        validator.require('org')
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')
        validator.mutually_exclude('environment', 'uuid')

    def run(self):
        name = self.get_option('name')
        org = self.get_option('org')
        env_name = self.get_option('environment')
        sys_uuid = self.get_option('uuid')

        try:
            system = get_system(org, name, env_name, sys_uuid)

        except ServerRequestError, e:
            if e[0] == 404:
                return os.EX_DATAERR
            else:
                raise e

        self.api.unregister(system['uuid'])
        print _("Successfully unregistered System [ %s ]") % name
        return os.EX_OK

class Subscribe(SystemAction):

    description = _('subscribe a system to certificate')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        parser.add_option('--name', dest='name',
                help=_("system name (required)"))
        parser.add_option('--uuid', dest='uuid',
                help=constants.OPT_HELP_SYSTEM_UUID)
        parser.add_option('--pool', dest='pool',
                help=_("certificate serial to unsubscribe (required)"))
        parser.add_option('--quantity', dest='quantity',
                help=_("quantity (default: 1)"))

    def check_options(self, validator):
        validator.require(('org', 'pool'))
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')

    def run(self):
        name = self.get_option('name')
        org = self.get_option('org')
        pool = self.get_option('pool')
        qty = self.get_option('quantity') or 1
        sys_uuid = self.get_option('uuid')

        system = get_system(org, name, sys_uuid = sys_uuid)

        self.api.subscribe(system['uuid'], pool, qty)
        print _("Successfully subscribed System [ %s ]") % name
        return os.EX_OK

class Subscriptions(SystemAction):

    description = _('list subscriptions for a system')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        parser.add_option('--name', dest='name', help=_("system name (required)"))
        parser.add_option('--uuid', dest='uuid', help=constants.OPT_HELP_SYSTEM_UUID)
        parser.add_option('--available', dest='available',
                action="store_true", default=False,
                help=_("show available subscriptions"))
        parser.add_option('--match_system', dest='match_system',
                action="store_true", default=False,
                help=_("show available subscriptions matching system"))
        parser.add_option('--match_installed', dest='match_installed',
                action="store_true", default=False,
                help=_("show available subscriptions matching installed software"))
        parser.add_option('--no_overlap', dest='no_overlap',
                action="store_true", default=False,
                help=_("show available subscriptions not overlapping current subscriptions"))

    def check_options(self, validator):
        validator.require('org')
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')

    def run(self):
        name = self.get_option('name')
        org = self.get_option('org')
        available = self.get_option('available')
        match_system = self.get_option('match_system')
        match_installed = self.get_option('match_installed')
        no_overlap = self.get_option('no_overlap')
        uuid = self.get_option('uuid')

        if not uuid:
            uuid = get_system(org, name)['uuid']

        self.printer.set_strategy(VerboseStrategy())
        if not available:
            # listing current subscriptions
            result = self.api.subscriptions(uuid)
            if result == None or len(result['entitlements']) == 0:
                print _("No Subscriptions found for System [ %s ] in Org [ %s ]") % (name, org)
                return os.EX_OK

            def entitlements():
                for entitlement in result['entitlements']:
                    entitlement_ext = entitlement.copy()
                    provided_products = ', '.join([e['name'] for e in entitlement_ext['providedProducts']])
                    entitlement_ext['providedProductsFormatted'] = provided_products
                    serial_ids = ', '.join([u_str(s['id']) for s in entitlement_ext['serials']])
                    entitlement_ext['serialIds'] = serial_ids
                    yield entitlement_ext

            self.printer.set_header(_("Current Subscriptions for System [ %s ]") % name)
            self.printer.add_column('entitlementId')
            self.printer.add_column('serialIds', name=_('Serial ID'))
            self.printer.add_column('poolName')
            self.printer.add_column('expires')
            self.printer.add_column('consumed')
            self.printer.add_column('quantity')
            self.printer.add_column('sla')
            self.printer.add_column('contractNumber')
            self.printer.add_column('providedProductsFormatted', name=_('Provided products'))
            self.printer.print_items(entitlements())
        else:
            # listing available pools
            result = self.api.available_pools(uuid, match_system, match_installed, no_overlap)

            if result == None or len(result) == 0:
                print _("No Pools found for System [ %s ] in Org [ %s ]") % (name, org)
                return os.EX_OK

            def available_pools():
                for pool in result['pools']:
                    pool_ext = pool.copy()
                    provided_products = ', '.join([p['productName'] for p in pool_ext['providedProducts']])
                    pool_ext['providedProductsFormatted'] = provided_products

                    if pool_ext['quantity'] == -1:
                        pool_ext['quantity'] = _('Unlimited')

                    # Make the productAttributes easier to access
                    for productAttribute in pool['productAttributes']:
                        pool_ext['attr_' + productAttribute['name']] = productAttribute['value']
                    yield pool_ext

            self.printer.set_header(_("Available Subscriptions for System [ %s ]") % name)
            self.printer.add_column('id')
            self.printer.add_column('productName', name=_('Name'))
            self.printer.add_column('endDate')
            self.printer.add_column('consumed')
            self.printer.add_column('quantity')
            self.printer.add_column('sockets')
            self.printer.add_column('attr_stacking_id', name=_('Stacking ID'))
            self.printer.add_column('attr_multi-entitlement', name=_('Multi-entitlement'))
            self.printer.add_column('providedProductsFormatted', name=_('Provided products'))
            self.printer.print_items(available_pools())

        return os.EX_OK

class Unsubscribe(SystemAction):

    description = _('unsubscribe a system from certificate')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        parser.add_option('--name', dest='name',
            help=_("system name (required)"))
        parser.add_option('--uuid', dest='uuid',
                help=constants.OPT_HELP_SYSTEM_UUID)
        parser.add_option('--entitlement', dest='entitlement',
            help=_("entitlement ID to unsubscribe from (either entitlement or serial or all is required)"))
        parser.add_option('--serial', dest='serial',
            help=_("serial ID of a certificate to unsubscribe from (either entitlement or serial or all is required)"))
        parser.add_option('--all', dest='all', action="store_true", default=None,
            help=_("unsubscribe from all currently subscribed certificates (either entitlement or serial or all is"
                + " required)"))

    def check_options(self, validator):
        validator.require('org')
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')
        validator.require_one_of(('entitlement', 'serial', 'all'))

    def run(self):
        name = self.get_option('name')
        org = self.get_option('org')
        entitlement = self.get_option('entitlement')
        serial = self.get_option('serial')
        all_entitlements = self.get_option('all')
        uuid = self.get_option('uuid')

        if not uuid:
            uuid = get_system(org, name)['uuid']

        if all_entitlements: #unsubscribe from all
            self.api.unsubscribe_all(uuid)
        elif serial: # unsubscribe from cert
            self.api.unsubscribe_by_serial(uuid, serial)
        elif entitlement: # unsubscribe from entitlement
            self.api.unsubscribe(uuid, entitlement)
        print _("Successfully unsubscribed System [ %s ]") % name

        return os.EX_OK

class Update(SystemAction):

    description = _('update a system')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        parser.add_option('--name', dest='name',
                       help=_('system name (required)'))
        parser.add_option('--uuid', dest='uuid',
                       help=constants.OPT_HELP_SYSTEM_UUID)
        opt_parser_add_environment(parser)
        parser.add_option('--new_name', dest='new_name',
                       help=_('a new name for the system'))
        parser.add_option('--new_environment', dest='new_environment',
                       help=_('a new environment name for the system'))
        parser.add_option('--description', dest='description',
                       help=_('a description of the system'))
        parser.add_option('--location', dest='location',
                       help=_("location of the system"))
        parser.add_option('--release', dest='release',
                       help=_("value of $releasever for the system"))
        parser.add_option('--servicelevel', dest='sla',
                       help=_("service level agreement"))

    def check_options(self, validator):
        validator.require('org')
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')
        validator.mutually_exclude('environment', 'uuid')

    def run(self):
        org_name = self.get_option('org')
        env_name = self.get_option('environment')
        sys_name = self.get_option('name')
        new_name = self.get_option('new_name')
        new_environment_name = self.get_option('new_environment')
        new_description = self.get_option('description')
        new_location = self.get_option('location')
        new_release = self.get_option('release')
        new_sla = self.get_option('sla')
        sys_uuid = self.get_option('uuid')

        system = get_system(org_name, sys_name, env_name, sys_uuid)
        new_environment = get_environment(org_name, new_environment_name)

        updates = {}
        if new_name:
            updates['name'] = new_name
        if new_description:
            updates['description'] = new_description
        if new_location:
            updates['location'] = new_location
        if new_release:
            updates['releaseVer'] = new_release
        if new_sla:
            updates['serviceLevel'] = new_sla
        if new_environment_name:
            new_environment = get_environment(org_name, new_environment_name)
            updates['environment_id'] = new_environment['id']

        response = self.api.update(system['uuid'], updates)

        test_record(response,
            _("Successfully updated system [ %s ]") % system['name'],
            _("Could not update system [ %s ]") % system['name']
        )


class Report(SystemAction):

    description = _('systems report')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        opt_parser_add_environment(parser)
        parser.add_option('--format', dest='format',
             help=_("report format (possible values: 'html', 'text' (default), 'csv', 'pdf')"))

    def check_options(self, validator):
        validator.require('org')

    def run(self):
        orgId = self.get_option('org')
        envName = self.get_option('environment')
        format_in = self.get_option('format')

        if envName is None:
            report = self.api.report_by_org(orgId, convert_to_mime_type(format_in, 'text'))
        else:
            environment = get_environment(orgId, envName)
            report = self.api.report_by_env(environment['id'], convert_to_mime_type(format_in, 'text'))

        if format_in == 'pdf':
            save_report(report[0], attachment_file_name(report[1], 'katello_systems_report.pdf'))
        else:
            print report[0]

        return os.EX_OK


class AddSystemGroups(SystemAction):

    description = _('add system groups to a system')

    def setup_parser(self, parser):
        parser.add_option('--name', dest='name',
                               help=_("system name (required)"))
        parser.add_option('--uuid', dest='uuid',
                              help=constants.OPT_HELP_SYSTEM_UUID)
        opt_parser_add_org(parser, required=1)
        parser.add_option('--system_groups', dest='system_group_names',
                              help=_("comma separated list of system group names (required)"))

    def check_options(self, validator):
        validator.require(('org', 'system_group_names'))
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')

    def run(self):
        org_name = self.get_option('org')
        sys_name = self.get_option('name')
        system_group_names = self.get_option('system_group_names')
        sys_uuid = self.get_option('uuid')

        query = {}
        update_dict_unless_none(query, "name", sys_name)
        update_dict_unless_none(query, "uuid", sys_uuid)
        system = self.api.systems_by_org(org_name, query)

        if system is None or len(system) == 0:
            return os.EX_DATAERR
        elif len(system) > 1:
            print constants.OPT_ERR_SYSTEM_AMBIGUOUS
            return os.EX_DATAERR
        else:
            system = system[0]

        system_groups = SystemGroupAPI().system_groups(org_name, { 'name' : system_group_names})

        if system_groups is None:
            return os.EX_DATAERR

        system_group_ids = [group["id"] for group in system_groups]

        system = self.api.add_system_groups(system["uuid"], system_group_ids)

        if system != None:
            print _("Successfully added system groups to system [ %s ]") % system['name']
            return os.EX_OK
        else:
            return os.EX_DATAERR


class RemoveSystemGroups(SystemAction):

    description = _('remove system groups to a system')

    def setup_parser(self, parser):
        parser.add_option('--name', dest='name',
                               help=_("system name (required)"))
        parser.add_option('--uuid', dest='uuid',
                               help=constants.OPT_HELP_SYSTEM_UUID)
        opt_parser_add_org(parser, required=1)
        parser.add_option('--system_groups', dest='system_group_names',
                              help=_("comma separated list of system group names (required)"))

    def check_options(self, validator):
        validator.require(('org', 'system_group_names'))
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')

    def run(self):
        org_name = self.get_option('org')
        sys_name = self.get_option('name')
        system_group_names = self.get_option('system_group_names')
        sys_uuid = self.get_option('uuid')

        query = {}
        update_dict_unless_none(query, "name", sys_name)
        update_dict_unless_none(query, "uuid", sys_uuid)
        system = self.api.systems_by_org(org_name, query)

        if system is None or len(system) == 0:
            return os.EX_DATAERR
        elif len(system) > 1:
            print constants.OPT_ERR_SYSTEM_AMBIGUOUS
            return os.EX_DATAERR
        else:
            system = system[0]

        system_groups = SystemGroupAPI().system_groups(org_name, { 'name' : system_group_names})

        if system_groups is None:
            return os.EX_DATAERR

        system_group_ids = [group["id"] for group in system_groups]

        system = self.api.remove_system_groups(system["uuid"], system_group_ids)

        if system != None:
            print _("Successfully removed system groups from system [ %s ]") % system['name']
            return os.EX_OK
        else:
            return os.EX_DATAERR


class AddCustomInfo(SystemAction):

    description = _('add custom infomation to a system')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        opt_parser_add_environment(parser)
        parser.add_option('--name', dest='name', help=_("System name (required)"))
        parser.add_option('--uuid', dest='uuid', help=constants.OPT_HELP_SYSTEM_UUID)
        parser.add_option('--keyname', dest='keyname', help=_("name to identify the custom info (required)"))
        parser.add_option('--value', dest='value', help=_("the custom info (required)"))

    def check_options(self, validator):
        validator.require(('org', 'keyname', 'value'))
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')
        validator.mutually_exclude('environment', 'uuid')

    def run(self):
        org_name = self.get_option('org')
        env_name = self.get_option("environment")
        sys_name = self.get_option('name')
        sys_uuid = self.get_option('uuid')
        keyname = self.get_option('keyname')
        value = self.get_option('value')

        system = get_system(org_name, sys_name, env_name, sys_uuid)

        custom_info_api = CustomInfoAPI()

        response = custom_info_api.add_custom_info("system", system['id'], keyname, value)

        ident = sys_uuid if sys_uuid else sys_name

        if response[keyname][0] == value:
            print _("Successfully added Custom Information [ %s : %s ] to System [ %s ]") % (keyname, value, ident)
        else:
            print _("Could not add Custom Information [ %s : %s ] to System [ %s ]") % (keyname, value, ident)

class ViewCustomInfo(SystemAction):

    description = _('view custom info attached to a system')

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        opt_parser_add_environment(parser)
        parser.add_option('--name', dest='name', help=_("System name (required)"))
        parser.add_option('--uuid', dest='uuid', help=constants.OPT_HELP_SYSTEM_UUID)
        parser.add_option('--keyname', dest='keyname', help=_("name of the custom info"))

    def check_options(self, validator):
        validator.require('org')
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')
        validator.mutually_exclude('environment', 'uuid')

    def run(self):
        org_name = self.get_option('org')
        env_name = self.get_option('environment')
        sys_name = self.get_option('name')
        sys_uuid = self.get_option('uuid')
        keyname = self.get_option('keyname')

        system = get_system(org_name, sys_name, env_name, sys_uuid)

        custom_info_api = CustomInfoAPI()

        custom_info = custom_info_api.get_custom_info("system", system['id'], keyname)

        for k in sorted(custom_info.keys()):
            self.printer.add_column(k, k)

        if sys_uuid:
            self.printer.set_header(_("Custom Information For System [ %s ]") % sys_uuid)
        elif env_name is None:
            self.printer.set_header(_("Custom Information For System [ %s ] in Org [ %s ]") % (sys_name, org_name))
        else:
            self.printer.set_header(_("Custom Information For System [ %s ] in Environment [ %s ] in Org [ %s ]") % (sys_name, env_name, org_name))
        self.printer.print_item(custom_info)


class UpdateCustomInfo(SystemAction):

    description = _("update custom info for a system")

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        opt_parser_add_environment(parser)
        parser.add_option('--name', dest='name', help=_("System name (required)"))
        parser.add_option('--uuid', dest='uuid', help=constants.OPT_HELP_SYSTEM_UUID)
        parser.add_option('--keyname', dest='keyname', help=_("name of the custom info"))
        parser.add_option('--current-value', dest='current-value', help=_("old value to update"))
        parser.add_option('--new-value', dest='new-value', help=_("replacement value"))

    def check_options(self, validator):
        validator.require(('org', 'keyname', 'current-value', 'new-value'))
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')
        validator.mutually_exclude('environment', 'uuid')

    def run(self):
        org_name = self.get_option('org')
        env_name = self.get_option('environment')
        sys_name = self.get_option('name')
        sys_uuid = self.get_option('uuid')
        keyname = self.get_option('keyname')
        current_value = self.get_option('current-value')
        new_value = self.get_option('new-value')

        system = get_system(org_name, sys_name, env_name, sys_uuid)

        custom_info_api = CustomInfoAPI()

        response = custom_info_api.update_custom_info("system", system['id'], keyname, current_value, new_value)

        ident = sys_uuid if sys_uuid else sys_name

        if response[keyname][0] == new_value:
            print _("Successfully updated Custom Information for System [ %s ]") % ident
        else:
            print _("Could not update Custom Information for System [ %s ]") % ident


class RemoveCustomInfo(SystemAction):

    description = _("remove custom info from a system")

    def setup_parser(self, parser):
        opt_parser_add_org(parser, required=1)
        opt_parser_add_environment(parser)
        parser.add_option('--name', dest='name', help=_("System name (required)"))
        parser.add_option('--uuid', dest='uuid', help=constants.OPT_HELP_SYSTEM_UUID)
        parser.add_option('--keyname', dest='keyname', help=_("name of the custom info"))
        parser.add_option('--value', dest='value', help=_("value of the custom info"))

    def check_options(self, validator):
        validator.require('org')
        validator.require_at_least_one_of(('name', 'uuid'))
        validator.mutually_exclude('name', 'uuid')
        validator.mutually_exclude('environment', 'uuid')

    def run(self):
        org_name = self.get_option('org')
        env_name = self.get_option('environment')
        sys_name = self.get_option('name')
        sys_uuid = self.get_option('uuid')
        keyname = self.get_option('keyname')
        value = self.get_option('value')

        system = get_system(org_name, sys_name, env_name, sys_uuid)

        custom_info_api = CustomInfoAPI()

        response = custom_info_api.remove_custom_info("system", system['id'], keyname, value)

        ident = sys_uuid if sys_uuid else sys_name

        if response:
            print _("Successfully removed Custom Information from System [ %s ]") % ident
        else:
            print _("Could not remove Custom Information from System [ %s ]") % ident


class System(Command):

    description = _('system specific actions in the katello server')
