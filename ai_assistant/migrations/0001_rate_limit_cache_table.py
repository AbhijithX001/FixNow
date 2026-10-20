from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE TABLE IF NOT EXISTS ai_rate_limit_cache ("
                "cache_key varchar(255) NOT NULL PRIMARY KEY, "
                "value text NOT NULL, "
                "expires datetime NOT NULL"
                ")"
            ),
            reverse_sql="DROP TABLE IF EXISTS ai_rate_limit_cache",
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX IF NOT EXISTS ai_rate_limit_cache_expires "
                "ON ai_rate_limit_cache (expires)"
            ),
            reverse_sql=(
                "DROP INDEX IF EXISTS ai_rate_limit_cache_expires"
            ),
        ),
    ]
