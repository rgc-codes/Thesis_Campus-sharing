from django.apps import AppConfig
from django.db.backends.signals import connection_created


def configure_sqlite_wal(sender, connection, **kwargs):
    """Enforce Write-Ahead Logging (WAL) and busy timeout on SQLite connections."""
    if connection.vendor == 'sqlite':
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL;')
            cursor.execute('PRAGMA busy_timeout=5000;')
            cursor.execute('PRAGMA synchronous=NORMAL;')


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'backend.core'
    label = 'core'

    def ready(self):
        connection_created.connect(configure_sqlite_wal)
