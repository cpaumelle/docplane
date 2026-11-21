import subprocess, time, uuid
from pathlib import Path

name = 'observer-review-' + uuid.uuid4().hex[:12]
image = 'postgres:16-alpine'
def run(args, **kw):
    return subprocess.run(args, text=True, capture_output=True, **kw)
def sql(query, user='postgres'):
    return run(['docker', 'exec', '-i', name, 'psql', '-h', '/var/run/postgresql', '-U', user, '-d', 'postgres', '-X', '-v', 'ON_ERROR_STOP=1'], input=query)
try:
    result = run(['docker', 'run', '-d', '--rm', '--network', 'none', '--name', name, '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', image])
    if result.returncode: raise RuntimeError('container start failed')
    for _ in range(120):
        logs = run(['docker', 'logs', name])
        initialized = 'PostgreSQL init process complete; ready for start up.' in logs.stdout
        if initialized and run(['docker', 'exec', name, 'pg_isready', '-U', 'postgres']).returncode == 0: break
        time.sleep(.25)
    else: raise RuntimeError('database startup failed')
    setup = '''CREATE SCHEMA work; CREATE SCHEMA docplane;
    CREATE TABLE docplane.principals (principal_id int PRIMARY KEY, display_name text, authorization_mode text, status text);
    CREATE TABLE docplane.api_tokens (principal_id int, token_id int, revoked_at timestamptz, expires_at timestamptz);
    CREATE TABLE work.initiatives (initiative_id int, initiative_key text);
    CREATE TABLE work.collaboration_grants (principal_id int, initiative_id int, participant_role text, expires_at timestamptz, revoked_at timestamptz);
    CREATE ROLE unsafe_member; CREATE ROLE docplane_credential_expiry_observer LOGIN; GRANT unsafe_member TO docplane_credential_expiry_observer;
    GRANT SELECT ON docplane.principals TO docplane_credential_expiry_observer;'''
    setup_result = sql(setup)
    if setup_result.returncode: raise RuntimeError('fixture setup failed: ' + setup_result.stderr)
    migration = Path('db/migrations/021_work_collaborator_credential_expiry.sql').read_text()
    denied = sql('BEGIN;\n' + migration + '\nCOMMIT;')
    if denied.returncode == 0 or 'already exists' not in denied.stderr: raise RuntimeError('pre-existing observer not refused')
    if sql('DROP OWNED BY docplane_credential_expiry_observer; DROP ROLE docplane_credential_expiry_observer; DROP ROLE unsafe_member;').returncode: raise RuntimeError('fixture reset failed')
    if sql('GRANT SELECT ON docplane.principals TO PUBLIC;').returncode: raise RuntimeError('public fixture setup failed')
    public_denied = sql('BEGIN;\n' + migration + '\nCOMMIT;')
    if public_denied.returncode == 0 or 'unexpected application table privilege' not in public_denied.stderr: raise RuntimeError('unexpected PUBLIC grant not refused')
    if sql('REVOKE SELECT ON docplane.principals FROM PUBLIC;').returncode: raise RuntimeError('public fixture reset failed')
    for privilege in ('SELECT', 'INSERT', 'UPDATE', 'REFERENCES'):
        grant = f'GRANT {privilege} (initiative_key) ON work.initiatives TO PUBLIC;'
        revoke = f'REVOKE {privilege} (initiative_key) ON work.initiatives FROM PUBLIC;'
        if sql(grant).returncode: raise RuntimeError('column fixture grant failed: ' + privilege)
        denied = sql('BEGIN;\n' + migration + '\nCOMMIT;')
        if denied.returncode == 0 or 'unexpected application column privilege' not in denied.stderr:
            raise RuntimeError('unexpected PUBLIC column grant not refused: ' + privilege)
        if sql(revoke).returncode: raise RuntimeError('column fixture reset failed: ' + privilege)
    # The views do not exist before this migration. Inject a synthetic PUBLIC
    # grant after view creation and before the audit, within the same transaction.
    for view in ('collaborator_token_expiry', 'collaborator_grant_expiry'):
        for privilege in ('INSERT', 'UPDATE', 'REFERENCES'):
            injected = migration.replace('-- Fail the migration,', f'GRANT {privilege} (participant) ON work.{view} TO PUBLIC;\n-- Fail the migration,', 1)
            if injected == migration: raise RuntimeError('view audit injection point missing')
            denied = sql('BEGIN;\n' + injected + '\nCOMMIT;')
            if denied.returncode == 0 or 'column write privilege on an expiry view' not in denied.stderr:
                raise RuntimeError('unexpected PUBLIC view column grant not refused: ' + view + ' ' + privilege)
    created = sql('BEGIN;\n' + migration + '\nCOMMIT;')
    if created.returncode: raise RuntimeError('clean migration failed')
    if sql('SELECT * FROM work.collaborator_token_expiry; SELECT * FROM work.collaborator_grant_expiry;', 'docplane_credential_expiry_observer').returncode: raise RuntimeError('views not readable')
    for query in ['SELECT * FROM docplane.principals;', 'INSERT INTO work.initiatives VALUES (1, \'x\');', 'SET ROLE postgres;']:
        if sql(query, 'docplane_credential_expiry_observer').returncode == 0: raise RuntimeError('unexpected role privilege')
    hba = 'local all all trust\nhost all all 127.0.0.1/32 scram-sha-256\nhost all all ::1/128 scram-sha-256\n'
    written = run(['docker', 'exec', '-i', name, 'sh', '-c', 'cat > "$PGDATA/pg_hba.conf"'], input=hba)
    if written.returncode or sql('SELECT pg_reload_conf();').returncode: raise RuntimeError('fixture HBA setup failed')
    time.sleep(1)
    tcp = run(['docker', 'exec', name, 'psql', '-w', '-h', '127.0.0.1', '-U', 'docplane_credential_expiry_observer', '-d', 'postgres', '-c', 'SELECT 1'])
    if tcp.returncode == 0: raise RuntimeError('passwordless observer authenticated over TCP')
    if sql('SELECT 1;', 'docplane_credential_expiry_observer').returncode: raise RuntimeError('local observer login failed after HBA reload')
    print('PASS: existing role and unexpected PUBLIC table and column grants refused; fresh role reads views and is denied base table, write, role switch and TCP')
finally:
    run(['docker', 'rm', '-f', name])
