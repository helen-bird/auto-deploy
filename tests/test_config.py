import tempfile
from pathlib import Path
import unittest
from autodeploy.core import Error, load_config


class ConfigTest(unittest.TestCase):
    def test_trd_style_yaml(self):
        config = load_config('deployment.example.yaml')
        self.assertEqual(config['repository']['branch'], 'main')
        self.assertIs(config['deployment']['approval_required'], True)
        self.assertEqual(config['schedule']['time'], '09:00')
        self.assertEqual(config['service']['command'], ['python', 'main.py'])

    def test_python_object_tags_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.yaml'
            path.write_text('!!python/object/apply:os.getpid []')
            with self.assertRaises(Error):
                load_config(path)
