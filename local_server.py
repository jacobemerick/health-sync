"""Local dev server — wraps Lambda handlers as plain HTTP endpoints."""
import json
from flask import Flask, request, jsonify
from health_ingest.workouts_handler import handler as workouts_handler
from health_ingest.metrics_handler import handler as metrics_handler

app = Flask(__name__)


@app.route("/", methods=["POST"])
def invoke_workouts():
    event = {"body": request.get_data(as_text=True)}
    result = workouts_handler(event, {})
    return jsonify(json.loads(result["body"])), result["statusCode"]


@app.route("/metrics", methods=["POST"])
def invoke_metrics():
    event = {"body": request.get_data(as_text=True)}
    result = metrics_handler(event, {})
    return jsonify(json.loads(result["body"])), result["statusCode"]


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000, debug=True)
