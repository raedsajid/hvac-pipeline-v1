"""Run once against a TEST database first, then during the production cutover."""
from builderp import create_app
from builderp.store import init_db
app = create_app({'AUTO_MIGRATE': False})
with app.app_context():
    init_db()
print('BuildERP schema initialized; existing records retained.')
