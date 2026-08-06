import argparse
import inspect
from unittest.mock import patch


class TestSpawnProcessTemplateDir:
    """Tests for template_dir resolution in SpawnProcess."""

    def _make_args(self, template_dir=None):
        """Create a minimal args Namespace."""
        return argparse.Namespace(
            host='127.0.0.1',
            port=18900,
            notify_host='127.0.0.1',
            notify_port=8991,
            workers=1,
            debug=False,
            enable_notify=False,  # don't actually spawn processes
            notify_empty=False,
            wkname='TestWorker',
            health_port=8080,
            template_dir=template_dir,
        )

    @patch('qw.process.is_port_available', return_value=True)
    @patch('qw.process.mp.Manager')
    @patch('qw.process.mp.Process')
    @patch('qw.process.ProcessSupervisor')
    @patch('qw.process.start_server')
    def test_template_dir_from_args(self, _srv, _sup, _proc, _mgr, _port):
        """CLI arg template_dir is stored on SpawnProcess."""
        from qw.process import SpawnProcess
        _mgr.return_value.dict.return_value = {}
        args = self._make_args(template_dir='/opt/templates')
        sp = SpawnProcess(args)
        assert sp._template_dir == '/opt/templates'

    @patch('qw.process.is_port_available', return_value=True)
    @patch('qw.process.mp.Manager')
    @patch('qw.process.mp.Process')
    @patch('qw.process.ProcessSupervisor')
    @patch('qw.process.start_server')
    @patch('qw.process.TEMPLATE_DIR', '/conf/templates')
    def test_template_dir_fallback_to_conf(self, _srv, _sup, _proc, _mgr, _port):
        """Falls back to TEMPLATE_DIR from conf when CLI arg is None."""
        from qw.process import SpawnProcess
        _mgr.return_value.dict.return_value = {}
        args = self._make_args(template_dir=None)
        sp = SpawnProcess(args)
        assert sp._template_dir == '/conf/templates'

    @patch('qw.process.is_port_available', return_value=True)
    @patch('qw.process.mp.Manager')
    @patch('qw.process.mp.Process')
    @patch('qw.process.ProcessSupervisor')
    @patch('qw.process.start_server')
    @patch('qw.process.TEMPLATE_DIR', None)
    def test_template_dir_none_when_both_unset(self, _srv, _sup, _proc, _mgr, _port):
        """template_dir is None when both CLI and conf are unset."""
        from qw.process import SpawnProcess
        _mgr.return_value.dict.return_value = {}
        args = self._make_args(template_dir=None)
        sp = SpawnProcess(args)
        assert sp._template_dir is None

    @patch('qw.process.is_port_available', return_value=True)
    @patch('qw.process.mp.Manager')
    @patch('qw.process.mp.Process')
    @patch('qw.process.ProcessSupervisor')
    @patch('qw.process.start_server')
    def test_notify_process_spawn_args_include_template_dir(
        self, _srv, _sup, _proc, _mgr, _port
    ):
        """The notify-process mp.Process(args=...) tuple carries the
        resolved template_dir as its 6th (last) positional argument, in
        the exact order start_notify_worker expects it."""
        from qw.process import SpawnProcess
        _mgr.return_value.dict.return_value = {}
        args = self._make_args(template_dir='/opt/templates')
        args.enable_notify = True
        sp = SpawnProcess(args)

        notify_calls = [
            call for call in _proc.call_args_list
            if call.kwargs.get('target') == sp.start_notify_worker
        ]
        assert len(notify_calls) == 1, (
            "Expected exactly one mp.Process call targeting "
            "start_notify_worker"
        )
        spawn_args = notify_calls[0].kwargs['args']
        assert spawn_args == (
            args.notify_host,
            args.notify_port,
            args.debug,
            f'NotifyWorker_{sp.id}',
            args.notify_empty,
            sp._template_dir,
        )
        assert spawn_args[-1] == '/opt/templates'


class TestStartNotifyWorkerIntrospection:
    """Tests for the introspection guard on NotifyWorker."""

    def test_template_dir_passed_when_supported(self):
        """template_dir is forwarded when NotifyWorker accepts it."""
        from qw.process import SpawnProcess

        captured: dict = {}

        class FakeNotifyWorker:
            def __init__(self, *, host, port, debug, name,
                         notify_empty_stream, template_dir=None):
                captured['template_dir'] = template_dir

            async def start(self):
                pass

        with patch('qw.process.NotifyWorker', FakeNotifyWorker):
            sp = object.__new__(SpawnProcess)
            sp.start_notify_worker(
                host='0.0.0.0',
                port=8991,
                debug=False,
                name='test',
                notify_empty=False,
                template_dir='/opt/tpl',
            )

        assert captured['template_dir'] == '/opt/tpl'

    def test_template_dir_omitted_when_unsupported(self):
        """template_dir is NOT passed when NotifyWorker doesn't accept it."""
        from qw.process import SpawnProcess

        class OldNotifyWorker:
            def __init__(self, *, host, port, debug, name,
                         notify_empty_stream):
                pass  # no template_dir

            async def start(self):
                pass

        with patch('qw.process.NotifyWorker', OldNotifyWorker):
            sp = object.__new__(SpawnProcess)
            # Should NOT raise — introspection guard omits template_dir
            sp.start_notify_worker(
                host='0.0.0.0',
                port=8991,
                debug=False,
                name='test',
                notify_empty=False,
                template_dir='/opt/tpl',
            )

    def test_introspection_guard_detects_template_dir_param(self):
        """Sanity check: inspect.signature detects template_dir when present."""
        class FakeNotifyWorker:
            def __init__(self, *, host, port, debug, name,
                         notify_empty_stream, template_dir=None):
                pass

        sig = inspect.signature(FakeNotifyWorker.__init__)
        assert 'template_dir' in sig.parameters
