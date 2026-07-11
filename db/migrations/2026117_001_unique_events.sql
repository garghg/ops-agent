ALTER TABLE inventory_transactions
  ADD COLUMN event_id TEXT NOT NULL,
  ADD CONSTRAINT inventory_transactions_event_id_key UNIQUE (event_id);