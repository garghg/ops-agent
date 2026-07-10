CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE inventory_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
  name TEXT NOT NULL,
  quantity_on_hand NUMERIC(10, 2) NOT NULL DEFAULT 0,
  reorder_point NUMERIC(10, 2) NOT NULL,
  reorder_quantity NUMERIC(10, 2) NOT NULL,
  cost_per_unit NUMERIC(10, 2) NOT NULL,
  supplier TEXT,
  shelf_life_days INTEGER,
  last_restocked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now ()
);

CREATE TABLE inventory_transactions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
  item_id UUID NOT NULL REFERENCES inventory_items (id) ON DELETE RESTRICT,
  quantity_change NUMERIC(10, 2) NOT NULL,
  transaction_type TEXT NOT NULL CHECK (
    transaction_type IN ('restock', 'usage', 'waste', 'adjustment')
  ),
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now (),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now (),
  note TEXT
);

