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
