# Synthetic logistics fixtures

These fixtures reproduce only the field names and nesting observed in the
read-only TikTok logistics-detail response. All order, package, supplier,
tracking, event, and fulfillment values are synthetic.

No fixture may contain a real creator name, recipient name, phone number,
address, authentication token, cookie, store credential, or complete captured
platform response. A fixture captured manually with
`scripts/capture_logistics_fixture.py` must be reviewed by an operator before
it is added to git, even though the script recursively redacts sensitive keys.

The verified paths represented here are:

- `data.package_list[].tracking_no`
- `data.package_list[].logistic_supplier.supplier_name`
- `data.package_list[].logistic_detail.track_list[0].title`
- `data.package_list[].logistic_detail.track_list[0].time`
- `data.package_list[].logistic_detail.track_list[0].content`
- `data.package_list[].predict_delivery_time_text`
- `data.package_list[].fulfill_unit_id`

The fixtures intentionally do not invent standalone delivery-time or status
fields. Delivery time comes only from the latest `Delivered` event timestamp;
the predicted delivery timestamp is ETA only.
