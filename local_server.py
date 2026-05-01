"""Local dev server — wraps Lambda handler as a plain HTTP endpoint."""
import json
from flask import Flask, request, jsonify
from health_ingest.handler import handler as lambda_handler

app = Flask(__name__)


@app.route("/", methods=["POST"])
def invoke():
    event = {"body": request.get_data(as_text=True)}
    result = lambda_handler(event, {})
    return jsonify(json.loads(result["body"])), result["statusCode"]


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000, debug=True)
