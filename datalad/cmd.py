# emacs: -*- mode: python; py-indent-offset: 4; tab-width: 4; indent-tabs-mode: nil -*-
# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the datalad package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""
Wrapper for command and function calls, allowing for dry runs and output handling

"""


import subprocess
import sys
import logging
import os
import shutil
from datalad.support.exceptions import CommandError

lgr = logging.getLogger('datalad.cmd')


class Runner(object):
    """Provides a wrapper for calling functions and commands.

    An object of this class provides a methods calls shell commands or python functions,
    allowing for dry runs and output handling.

    Outputs (stdout and stderr) can be either logged or streamed to system's stdout/stderr during execution.
    This can be enabled or disabled for both of them independently.
    Additionally allows for dry runs. This is achieved by initializing the `Runner` with `dry=True`.
    The Runner will then collect all calls as strings in `commands`.
    """

    __slots__ = ['commands', 'dry']

    def __init__(self, dry=False):
        self.dry = dry
        self.commands = []

    def __call__(self, cmd, *args, **kwargs):
        """Convenience method

        This will call run() or call() depending on the kind of `cmd`.
        If `cmd` is a string it is interpreted as the to be executed command.
        Otherwise it is expected to be a callable.
        Any other argument is passed to the respective method.

        Parameters
        ----------
        cmd: str or callable
           command string to be executed via shell or callable to be called.

        *args and **kwargs:
           see Runner.run() and Runner.call() for available arguments.

        Raises
        ------
        ValueError if cmd is neither a string nor a callable.
        """

        if isinstance(cmd, basestring) or isinstance(cmd, list):
            return self.run(cmd, *args, **kwargs)
        elif callable(cmd):
            return self.call(cmd, *args, **kwargs)
        else:
            raise ValueError("Argument 'command' is neither a string, "
                             "nor a list nor a callable.")

    # Two helpers to encapsulate formatting/output
    def _log_out(self, line):
        if line:
            self.log("stdout| " + line.rstrip('\n'))

    def _log_err(self, line, expect_stderr=False):
        if line:
            self.log("stderr| " + line.rstrip('\n'),
                     level={True: logging.DEBUG,
                            False: logging.ERROR}[expect_stderr])

    def _get_output_online(self, proc, log_stdout, log_stderr, expect_stderr=False):
        stdout, stderr = [], []
        while proc.poll() is None:
            if log_stdout:
                line = proc.stdout.readline()
                if line != '':
                    stdout += line
                    self._log_out(line)
                    # TODO: what level to log at? was: level=5
                    # Changes on that should be properly adapted in
                    # test.cmd.test_runner_log_stdout()
            else:
                pass

            if log_stderr:
                line = proc.stderr.readline()
                if line != '':
                    stderr += line
                    self._log_err(line, expect_stderr)
                    # TODO: what's the proper log level here?
                    # Changes on that should be properly adapted in
                    # test.cmd.test_runner_log_stderr()
            else:
                pass

        return stdout, stderr

    def run(self, cmd, log_stdout=True, log_stderr=True,
            log_online=False, expect_stderr=False, cwd=None, shell=None):
        """Runs the command `cmd` using shell.

        In case of dry-mode `cmd` is just added to `commands` and it is executed otherwise.
        Allows for seperatly logging stdout and stderr  or streaming it to system's stdout
        or stderr respectively.

        Parameters
        ----------
        cmd : str, list
          String (or list) defining the command call.  No shell is used if cmd
          is specified as a list

        log_stdout: bool, optional
            If True, stdout is logged. Goes to sys.stdout otherwise.

        log_stderr: bool, optional
            If True, stderr is logged. Goes to sys.stderr otherwise.

        log_online: bool, optional
            Either to log as output comes in.  Setting to True is preferable for
            running user-invoked actions to provide timely output

        expect_stderr: bool, optional
            Normally, having stderr output is a signal of a problem and thus it
            gets logged at ERROR level.  But some utilities, e.g. wget, use
            stderr for their progress output.  Whenever such output is expected,
            set it to True and output will be logged at DEBUG level unless
            exit status is non-0 (in non-online mode only, in online -- would
            log at DEBUG)

        cwd : string, optional
            Directory under which run the command (passed to Popen)

        shell: bool, optional
            Run command in a shell.  If not specified, then it runs in a shell only
            if command is specified as a string (not a list)

        Returns
        -------
        (stdout, stderr)

        Raises
        ------
        CommandError
           if command's exitcode wasn't 0 or None. exitcode is passed to CommandError's `code`-field.
           command's stdout and stderr are stored in CommandError's `stdout` and `stderr` fields respectively.
        """

        outputstream = subprocess.PIPE if log_stdout else sys.stdout
        errstream = subprocess.PIPE if log_stderr else sys.stderr

        self.log("Running: %s" % (cmd,))

        if not self.dry:

            if shell is None:
                shell = isinstance(cmd, basestring)
            try:
                proc = subprocess.Popen(cmd, stdout=outputstream, stderr=errstream,
                                    shell=shell,
                                    cwd=cwd)
            except Exception, e:
                lgr.error("Failed to start %r%r: %s" % (cmd, " under %r" % cwd if cwd else '', e))
                raise
            # shell=True allows for piping, multiple commands, etc., but that implies to not use shlex.split()
            # and is considered to be a security hazard. So be careful with input.
            # Alternatively we would have to parse `cmd` and create multiple
            # subprocesses.

            if log_online:
                out = self._get_output_online(proc, log_stdout, log_stderr,
                                              expect_stderr=expect_stderr)
            else:
                out = proc.communicate()

            status = proc.poll()

            # needs to be done after we know status
            if not log_online:
                self._log_out(out[0])
                if status not in [0, None]:
                    self._log_err(out[1], expect_stderr=False)
                else:
                    # as directed
                    self._log_err(out[1], expect_stderr=expect_stderr)

            if status not in [0, None]:
                msg = "Failed to run %r%s. Exit code=%d" \
                    % (cmd, " under %r" % cwd if cwd else '', status)
                lgr.error(msg)
                raise CommandError(str(cmd), msg, status, out[0], out[1])
            else:
                self.log("Finished running %r with status %s" % (cmd, status), level=8)

        else:
            self.commands.append(cmd)
            out = ("DRY", "DRY")

        return out

    def call(self, f, *args, **kwargs):
        """Helper to unify collection of logging all "dry" actions.

        Calls `f` if `Runner`-object is not in dry-mode. Adds `f` along with its arguments to `commands` otherwise.

        f : callable
        *args, **kwargs:
          Callable arguments
        """
        if self.dry:
            self.commands.append("%s args=%s kwargs=%s" % (f, args, kwargs))
        else:
            return f(*args, **kwargs)

    def log(self, msg, level=logging.DEBUG):
        """log helper

        Logs at DEBUG-level by default and adds "DRY:"-prefix for dry runs.
        """
        if self.dry:
            lgr.log(level, "DRY: %s" % msg)
        else:
            lgr.log(level, msg)


# ####
# Preserve from previous version
# TODO: document intention
# ####
# this one might get under Runner for better output/control
def link_file_load(src, dst, dry_run=False):
    """Just a little helper to hardlink files's load
    """
    dst_dir = os.path.dirname(dst)
    if not os.path.exists(dst_dir):
        os.makedirs(dst_dir)
    if os.path.lexists(dst):
        lgr.debug("Destination file %(dst)s exists. Removing it first"
                  % locals())
        # TODO: how would it interact with git/git-annex
        os.unlink(dst)
    lgr.debug("Hardlinking %(src)s under %(dst)s" % locals())
    src_realpath = os.path.realpath(src)

    try:
        os.link(src_realpath, dst)
    except  AttributeError, e:
        lgr.warn("Linking of %s failed (%s), copying file" % (src, e))
        shutil.copyfile(src_realpath, dst)
        shutil.copystat(src_realpath, dst)
