const express = require('express');
const http = require('http');

class NotionMock {
  constructor() {
    this.calls = { pages: [], queries: [] };
    this.app = express();
    this.app.use(express.json());
    this.server = null;

    this.app.post('/v1/pages', (req, res) => {
      this.calls.pages.push(req.body);
      res.json({ id: 'mock-page-id-' + Date.now(), object: 'page' });
    });

    this.app.post('/v1/databases/:id/query', (req, res) => {
      this.calls.queries.push({ dbId: req.params.id, body: req.body });
      const sourceId = req.body?.filter?.rich_text?.equals;
      const results = sourceId
        ? this.calls.pages.filter(p =>
            p.properties?.['Source ID']?.rich_text?.[0]?.text?.content === sourceId
          ).map((_, i) => ({ id: `existing-page-${i}` }))
        : [];
      res.json({ results, has_more: false });
    });
  }

  start(port = 3001) {
    return new Promise(resolve => {
      this.server = this.app.listen(port, resolve);
    });
  }

  stop() {
    return new Promise(resolve => {
      if (this.server) this.server.close(resolve);
      else resolve();
    });
  }

  clear() {
    this.calls = { pages: [], queries: [] };
  }

  getPageCalls() { return this.calls.pages; }
  getQueryCalls() { return this.calls.queries; }
}

module.exports = NotionMock;
