from pathlib import Path
import tempfile

from django.test import SimpleTestCase, override_settings


class GoogleVerificationViewTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.temp_path = Path(self.tempdir.name)

    def test_serves_existing_verification_file(self):
        filename = 'googleabc123.html'
        file_contents = 'google-site-verification: googleabc123.html'
        (self.temp_path / filename).write_text(file_contents)

        with override_settings(GOOGLE_VERIFICATION_DIR=self.temp_path):
            response = self.client.get(f'/{filename}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/html')
        body = b''.join(response.streaming_content).decode('utf-8')
        self.assertEqual(body, file_contents)

    def test_missing_file_returns_404(self):
        with override_settings(GOOGLE_VERIFICATION_DIR=self.temp_path):
            response = self.client.get('/googlemissing.html')

        self.assertEqual(response.status_code, 404)
