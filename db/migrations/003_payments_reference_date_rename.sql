-- Olist's olist_order_payments_dataset.csv has no timestamp field at all
-- (only order_id, payment_sequential, payment_type, payment_installments,
-- payment_value). The `paid_at` column was actually populated from the
-- order's purchase date, not any real payment-channel event time -- naming
-- it `paid_at` implied a fact the source data never provided. Renamed to
-- payment_reference_date to make clear it's a derived reference point, not
-- an observed payment timestamp.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'paid_at'
    ) THEN
        ALTER TABLE payments RENAME COLUMN paid_at TO payment_reference_date;
    END IF;
END $$;

-- real Olist field, missing from 001 -- needed for stable ordering of a
-- split payment (voucher + card, etc.) across multiple rows for one order.
ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS payment_sequential INTEGER;

