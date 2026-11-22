import pymysql
from django.db.backends.mysql.schema import DatabaseSchemaEditor

pymysql.install_as_MySQLdb()

# TiDB Cloud Serverless compatibility:
# 1. TiDB tidak mendukung inline foreign key pada statement ADD COLUMN yang sama.
DatabaseSchemaEditor.sql_create_column_inline_fk = None

_orig_create_model = DatabaseSchemaEditor.create_model
_orig_add_field = DatabaseSchemaEditor.add_field
_orig_remove_field = DatabaseSchemaEditor.remove_field
_orig_execute = DatabaseSchemaEditor.execute

def _tidb_execute(self, sql, params=()):
    try:
        return _orig_execute(self, sql, params)
    except Exception as e:
        msg = str(e).lower()
        if 'already exists' in msg or 'duplicate column' in msg or 'duplicate key' in msg:
            return
        raise

def _tidb_create_model(self, model):
    table_name = model._meta.db_table
    try:
        with self.connection.cursor() as cursor:
            if table_name in self.connection.introspection.table_names(cursor):
                return
    except Exception:
        pass
    _orig_create_model(self, model)

def _tidb_add_field(self, model, field):
    table_name = model._meta.db_table
    try:
        with self.connection.cursor() as cursor:
            columns = [c.name for c in self.connection.introspection.get_table_description(cursor, table_name)]
            if field.column in columns:
                return
    except Exception:
        pass

    # TiDB Cloud tidak mendukung inline UNIQUE constraint saat ADD COLUMN (error 8200).
    # Buat kolom terlebih dahulu, lalu tambahkan UNIQUE constraint via deferred_sql.
    is_unique = getattr(field, 'unique', False) and not getattr(field, 'primary_key', False)
    if is_unique:
        field.unique = False
        try:
            _orig_add_field(self, model, field)
        finally:
            field.unique = True
        unique_sql = self._create_unique_sql(model, [field])
        if unique_sql:
            self.deferred_sql.append(unique_sql)
    else:
        _orig_add_field(self, model, field)

def _tidb_remove_field(self, model, field):
    table_name = model._meta.db_table
    try:
        with self.connection.cursor() as cursor:
            columns = [c.name for c in self.connection.introspection.get_table_description(cursor, table_name)]
            if field.column not in columns:
                return
    except Exception:
        pass
    _orig_remove_field(self, model, field)

DatabaseSchemaEditor.create_model = _tidb_create_model
DatabaseSchemaEditor.add_field = _tidb_add_field
DatabaseSchemaEditor.remove_field = _tidb_remove_field
DatabaseSchemaEditor.execute = _tidb_execute
