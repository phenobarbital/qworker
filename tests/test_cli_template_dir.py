import argparse


class TestCliTemplateDirArg:
    def _parse_args(self, argv: list[str]) -> argparse.Namespace:
        """Parse argv through the real _add_start_args parser."""
        from qw.__main__ import _add_start_args
        parser = argparse.ArgumentParser()
        _add_start_args(parser)
        return parser.parse_args(argv)

    def test_template_dir_provided(self):
        """--template-dir stores the path string."""
        args = self._parse_args(['--template-dir', '/opt/templates'])
        assert args.template_dir == '/opt/templates'

    def test_template_dir_default_none(self):
        """Omitting --template-dir yields None."""
        args = self._parse_args([])
        assert args.template_dir is None

    def test_template_dir_dest_name(self):
        """dest is 'template_dir' (underscore, not hyphen)."""
        args = self._parse_args(['--template-dir', '/tmp/tpl'])
        assert hasattr(args, 'template_dir')
