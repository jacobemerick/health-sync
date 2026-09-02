const express = require('express');

class NotionMock {
  constructor() {
    this.calls = { pages: [], queries: [], archives: [] };
    this.records = [];
    this.nextId = 0;
    this.app = express();
    this.app.use(express.json());
    this.server = null;

    this.app.post('/v1/pages', (req, res) => {
      this.calls.pages.push(req.body);
      const record = {
        // Notion's created_time only has second granularity, so pages written
        // in the same second tie — which is exactly how the real duplicates
        // were produced. Leave it truncated so the id tie-break gets exercised.
        id: 'page-' + String(this.nextId++).padStart(4, '0'),
        created_time: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
        archived: false,
        dbId: req.body.parent?.database_id,
        body: req.body,
      };
      this.records.push(record);
      res.json({ id: record.id, created_time: record.created_time, object: 'page' });
    });

    this.app.patch('/v1/pages/:id', (req, res) => {
      const record = this.records.find(r => r.id === req.params.id);
      if (!record) return res.status(404).json({ object: 'error' });
      if (req.body?.archived === true) {
        this.calls.archives.push(req.params.id);
        record.archived = true;
      }
      res.json({ id: record.id, archived: record.archived, object: 'page' });
    });

    this.app.post('/v1/databases/:id/query', (req, res) => {
      this.calls.queries.push({ dbId: req.params.id, body: req.body });

      const sourceId = req.body?.filter?.rich_text?.equals;
      const dateEquals = req.body?.filter?.date?.equals;
      // Scope to live pages in this specific DB
      const dbPages = this.records.filter(r => r.dbId === req.params.id && !r.archived);

      let results = [];
      if (sourceId) {
        results = dbPages.filter(
          r => r.body.properties?.['Source ID']?.rich_text?.[0]?.text?.content === sourceId
        );
      } else if (dateEquals) {
        results = dbPages.filter(
          r => r.body.properties?.['Date']?.date?.start === dateEquals
        );
      }

      res.json({
        results: results.map(r => ({ id: r.id, created_time: r.created_time })),
        has_more: false,
      });
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
    this.calls = { pages: [], queries: [], archives: [] };
    this.records = [];
    this.nextId = 0;
  }

  getPageCalls() { return this.calls.pages; }
  getQueryCalls() { return this.calls.queries; }
  getArchiveCalls() { return this.calls.archives; }

  // Pages that survived — i.e. what the Notion database would actually show.
  getLivePages() { return this.records.filter(r => !r.archived).map(r => r.body); }
}

module.exports = NotionMock;
