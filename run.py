"""Local entry point: python run.py. For production use the included container."""
import os
from builderp import create_app
app=create_app()
if __name__=='__main__':
    from urllib.parse import urlsplit
    address=urlsplit(app.config['APP_ORIGIN'])
    app.run(host=os.getenv('HOST','127.0.0.1'),port=int(os.getenv('PORT',str(address.port or 8000))),debug=False,threaded=False)
