from pathlib import Path
from unittest.mock import call, patch

from django.conf import settings
from django.test import SimpleTestCase

import manage


class ManageReindexDecisionTests(SimpleTestCase):
    def test_runserver_parent_process_does_not_reindex(self):
        argv = ['manage.py', 'runserver']
        environ = {}

        should_reindex = manage.should_reindex_on_runserver(argv, environ)

        self.assertFalse(should_reindex)

    def test_runserver_child_process_reindexes(self):
        argv = ['manage.py', 'runserver']
        environ = {'RUN_MAIN': 'true'}

        should_reindex = manage.should_reindex_on_runserver(argv, environ)

        self.assertTrue(should_reindex)

    def test_runserver_no_reload_reindexes(self):
        argv = ['manage.py', 'runserver', '--noreload']
        environ = {}

        should_reindex = manage.should_reindex_on_runserver(argv, environ)

        self.assertTrue(should_reindex)

    def test_non_runserver_does_not_reindex(self):
        argv = ['manage.py', 'migrate']
        environ = {'RUN_MAIN': 'true'}

        should_reindex = manage.should_reindex_on_runserver(argv, environ)

        self.assertFalse(should_reindex)


class ManageRunserverPreparationTests(SimpleTestCase):
    @patch("manage.call_command")
    @patch("manage.django.setup")
    def test_migrates_before_reindexing(self, setup_mock, call_command_mock):
        manage.prepare_runserver()

        setup_mock.assert_called_once_with()
        self.assertEqual(
            call_command_mock.call_args_list,
            [
                call("migrate", interactive=False),
                call("reindex_search"),
            ],
        )


class ProductionStartupMigrationTests(SimpleTestCase):
    def test_docker_entrypoint_migrates_before_launching_server(self):
        project_root = Path(settings.BASE_DIR)
        dockerfile = (project_root / "Dockerfile").read_text()
        entrypoint = (project_root / "docker-entrypoint.sh").read_text()

        self.assertIn('ENTRYPOINT ["/app/docker-entrypoint.sh"]', dockerfile)
        migrate_position = entrypoint.index("python manage.py migrate --noinput")
        collectstatic_position = entrypoint.index("python manage.py collectstatic --noinput")
        exec_position = entrypoint.index('exec "$@"')
        self.assertLess(migrate_position, collectstatic_position)
        self.assertLess(collectstatic_position, exec_position)

    def test_deployment_waits_for_entrypoint_instead_of_migrating_concurrently(self):
        deployment_manager = (Path(settings.BASE_DIR) / "deployment-manager.py").read_text()
        method_start = deployment_manager.index("    def rebuild_and_restart_stack(")
        method_end = deployment_manager.index("\n    def ", method_start + 1)
        method_source = deployment_manager[method_start:method_end]

        self.assertIn("docker compose up --build -d --wait", method_source)
        self.assertNotIn("python manage.py migrate", method_source)
        self.assertNotIn("python manage.py collectstatic", method_source)

    def test_systemd_service_migrates_before_gunicorn(self):
        service = (Path(settings.BASE_DIR) / "gunicorn.service").read_text()

        migrate_position = service.index("ExecStartPre=")
        gunicorn_position = service.index("ExecStart=")
        self.assertIn("manage.py migrate --noinput", service[migrate_position:gunicorn_position])
        self.assertLess(migrate_position, gunicorn_position)
