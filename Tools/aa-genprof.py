#!/usr/bin/python
import sys
import subprocess
import os
import re
import atexit
import argparse

import apparmor.aa as apparmor

def sysctl_read(path):
    value = None
    with open(path, 'r') as f_in:
        value = int(f_in.readline())
    return value

def sysctl_write(path, value):
    if not value:
        return
    with open(path, 'w') as f_out:
        f_out.write(str(value))

def last_audit_entry_time():
    out = subprocess.check_output(['tail', '-1', '/var/log/audit/audit.log'], shell=True)
    logmark = None
    if re.search('^*msg\=audit\((\d+\.\d+\:\d+).*\).*$', out):
        logmark = re.search('^*msg\=audit\((\d+\.\d+\:\d+).*\).*$', out).groups()[0]
    else:
        logmark = ''
    return logmark

def restore_ratelimit():
    sysctl_write(ratelimit_sysctl, ratelimit_saved)

parser = argparse.ArgumentParser(description='Generate profile for the given program')
parser.add_argument('-d', type=str, help='path to profiles')
parser.add_argument('-f', type=str, help='path to logfile')
parser.add_argument('program', type=str, help='name of program to profile')
args = parser.parse_args()

profiling = args.program
profiledir = args.d
filename = args.f

aa_mountpoint = apparmor.check_for_apparmor()
if not aa_mountpoint:
    raise apparmor.AppArmorException(_('AppArmor seems to have not been started. Please enable AppArmor and try again.'))

if profiledir:
    apparmor.profile_dir = apparmor.get_full_path(profiledir)
    if not os.path.isdir(apparmor.profile_dir):
        raise apparmor.AppArmorException("Can't find AppArmor profiles in %s." %profiledir)


# if not profiling:
#     profiling = apparmor.UI_GetString(_('Please enter the program to profile: '), '')
#     if profiling:
#         profiling = profiling.strip()
#     else:
#         sys.exit(0)

program = None
#if os.path.exists(apparmor.which(profiling.strip())):
if os.path.exists(profiling):
    program = apparmor.get_full_path(profiling)
else:
    if '/' not in profiling:
        which = apparmor.which(profiling)
        if which:
            program = apparmor.get_full_path(which)

if not program or not os.path.exists(program):
    if '/' not in profiling:
        raise apparmor.AppArmorException(_("Can't find %s in the system path list. If the name of the application is correct, please run 'which %s' in another window in order to find the fully-qualified path.") %(profiling, profiling))
    else:
        raise apparmor.AppArmorException(_('%s does not exists, please double-check the path.') %profiling)

# Check if the program has been marked as not allowed to have a profile
apparmor.check_qualifiers(program)

apparmor.loadincludes()

profile_filename = apparmor.get_profile_filename(program)
if os.path.exists(profile_filename):
    apparmor.helpers[program] = apparmor.get_profile_flags(profile_filename)
else:
    apparmor.autodep(program)
    apparmor.helpers[program] = 'enforce'

if apparmor.helpers[program] == 'enforce':
    apparmor.complain(program)
    apparmor.reload(program)

# When reading from syslog, it is possible to hit the default kernel
# printk ratelimit. This will result in audit entries getting skipped,
# making profile generation inaccurate. When using genprof, disable
# the printk ratelimit, and restore it on exit.
ratelimit_sysctl = '/proc/sys/kernel/printk_ratelimit'
ratelimit_saved = sysctl_read(ratelimit_sysctl)
sysctl_write(ratelimit_sysctl, 0)

atexit.register(restore_ratelimit)

apparmor.UI_Info(_('\nBefore you begin, you may wish to check if a\nprofile already exists for the application you\nwish to confine. See the following wiki page for\nmore information:\nhttp://wiki.apparmor.net/index.php/Profiles'))

apparmor.UI_Important(_('Please start the application to be profiled in\nanother window and exercise its functionality now.\n\nOnce completed, select the "Scan" option below in \norder to scan the system logs for AppArmor events. \n\nFor each AppArmor event, you will be given the \nopportunity to choose whether the access should be \nallowed or denied.'))

syslog = True
logmark = ''
done_profiling = False

if os.path.exists('/var/log/audit/audit.log'):
    syslog = False

passno = 0
while not done_profiling:
    if syslog:
        logmark = subprocess.check_output(['date | md5sum'], shell=True)
        logmark = logmark.decode('ascii').strip()
        logmark = re.search('^([0-9a-f]+)', logmark).groups()[0]
        t=subprocess.call("%s -p kern.warn 'GenProf: %s'"%(apparmor.logger, logmark), shell=True)

    else:
        logmark = last_audit_entry_time()
    
    q=apparmor.hasher()
    q['headers'] = [_('Profiling'), program]
    q['functions'] = ['CMD_SCAN', 'CMD_FINISHED']
    q['default'] = 'CMD_SCAN'
    ans, arg = apparmor.UI_PromptUser(q, 'noexit')
    
    if ans == 'CMD_SCAN':
        lp_ret = apparmor.do_logprof_pass(logmark, passno)
        passno += 1
        if lp_ret == 'FINISHED':
            done_profiling = True
    else:
        done_profiling = True

for p in sorted(apparmor.helpers.keys()):
    if apparmor.helpers[p] == 'enforce':
        enforce(p)
        reload(p)

apparmor.UI_Info(_('\nReloaded AppArmor profiles in enforce mode.'))
apparmor.UI_Info(_('\nPlease consider contributing your new profile!\nSee the following wiki page for more information:\nhttp://wiki.apparmor.net/index.php/Profiles\n'))
apparmor.UI_Info(_('Finished generating profile for %s.')%program)
sys.exit(0)
