from flask import Flask, jsonify, render_template
from flask_cors import CORS
from data_recorder import DataRecorder
import logging

logger = logging.getLogger(__name__)

app = Flask(__name__, 
            template_folder='../templates',
            static_folder='../static')
CORS(app)

recorder = DataRecorder()


@app.route('/')
def index():
    return render_template('dashboard.html')


@app.route('/api/stats')
def get_stats():
    stats = recorder.get_stats_summary()
    return jsonify(stats)


@app.route('/api/prices')
def get_prices():
    prices = recorder.get_recent_prices(limit=100)
    return jsonify(prices)


@app.route('/api/opportunities')
def get_opportunities():
    opportunities = recorder.get_recent_opportunities(limit=50)
    return jsonify(opportunities)


@app.route('/api/trades')
def get_trades():
    trades = recorder.get_recent_trades(limit=50)
    return jsonify(trades)


@app.route('/api/pnl_history')
def get_pnl_history():
    history = recorder.get_pnl_history(hours=48)
    return jsonify(history)


@app.route('/api/health')
def health():
    return jsonify({'status': 'ok'})


def run_dashboard(host='0.0.0.0', port=8080):
    logger.info(f"Starting dashboard on http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == '__main__':
    run_dashboard()
