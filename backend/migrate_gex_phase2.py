"""
Run migration 007_gex_phase2.sql against the doMyTrade Supabase.

Usage:
    python3 migrate_gex_phase2.py

Falls back to printing the SQL if the API call fails — paste it into:
    https://supabase.com/dashboard/project/ziyzsnnhbckkusmcaiuh/sql/new
"""
import os
import sys
import pathlib
from dotenv import load_dotenv

load_dotenv()

SQL_FILE = pathlib.Path(__file__).parent / 'migrations' / '007_gex_phase2.sql'
SQL      = SQL_FILE.read_text()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

def run_via_api() -> bool:
    """Try Supabase REST SQL endpoint (service role required)."""
    try:
        import httpx
        # Supabase exposes a direct SQL query endpoint for service-role callers
        url = f'{SUPABASE_URL.rstrip("/")}/rest/v1/rpc/exec_sql'
        r = httpx.post(
            url,
            headers={
                'apikey'       : SERVICE_KEY,
                'Authorization': f'Bearer {SERVICE_KEY}',
                'Content-Type' : 'application/json',
            },
            json={'sql': SQL},
            timeout=30,
        )
        if r.status_code == 200:
            return True
        # Some Supabase projects expose the pg endpoint differently
        url2 = f'{SUPABASE_URL.rstrip("/")}/pg/query'
        r2 = httpx.post(
            url2,
            headers={
                'Authorization': f'Bearer {SERVICE_KEY}',
                'Content-Type' : 'application/json',
            },
            json={'query': SQL},
            timeout=30,
        )
        return r2.status_code == 200
    except Exception as e:
        print(f'  API attempt failed: {e}')
        return False


def run_via_supabase_client() -> bool:
    """Try via supabase-py rpc — only works if exec_sql function exists."""
    try:
        from supabase import create_client
        client = create_client(SUPABASE_URL, SERVICE_KEY)
        client.rpc('exec_sql', {'sql': SQL}).execute()
        return True
    except Exception as e:
        print(f'  supabase-py rpc attempt failed: {e}')
        return False


if __name__ == '__main__':
    print('=== GEX Phase 2 Migration ===')
    print(f'Target: {SUPABASE_URL}')
    print()

    if not SUPABASE_URL or not SERVICE_KEY:
        print('ERROR: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set in .env')
        sys.exit(1)

    print('Attempting API migration...')
    ok = run_via_api() or run_via_supabase_client()

    if ok:
        print('✅ Migration applied successfully.')
    else:
        print()
        print('⚠️  Automatic migration failed. Paste the SQL below into:')
        print(f'   https://supabase.com/dashboard/project/ziyzsnnhbckkusmcaiuh/sql/new')
        print()
        print('─' * 60)
        print(SQL)
        print('─' * 60)
