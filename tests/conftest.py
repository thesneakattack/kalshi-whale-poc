"""
Bootstraps pytest-time isolation from live repository data before any test
module in this directory is collected (and therefore before any test can
`import main` or a services module that constructs an app_state singleton).
The actual mechanism - what gets redirected, the sqlite3.connect hard guard,
and the collection-order incident that made per-file redirects insufficient
on their own - lives in tests/support/runtime_isolation.py.
"""
from tests.support.runtime_isolation import install_runtime_isolation

install_runtime_isolation()
