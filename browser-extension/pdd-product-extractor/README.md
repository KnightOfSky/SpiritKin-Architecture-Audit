# SpiritKin PDD Product Extractor

Manifest V3 browser capability migrated from
`E:\AutoProcessAP\pdd-batch-extractor`.

## Runtime flow

1. The Android bridge posts a web product link obtained from the PDD App to the
   SpiritKin control plane.
2. The extension automatically claims available phone links through
   `POST /extension/links/claim` using a `browser_extension` pairing token.
3. A background tab opens the product page in the already logged-in browser
   profile.
4. `page-bridge.js` reads `window.rawData`; `normalizer.js` converts it to
   `spiritkin.pdd_product_data.v1`.
5. The extension posts the result to `POST /extension/results`. Product JSON is
   stored as a control-plane Artifact, attached to the matching ecommerce task,
   and the mobile link becomes `completed`.

WeChat mini-program short links and the legacy OCR fallback are not supported.
The phone bridge accepts only PDD HTTP(S) product links that the logged-in
browser extension can open.

## Install

1. Start the SpiritKin control plane on port `8791`.
2. Create a pairing token with `device_role=browser_extension` from the
   authenticated control console.
3. Open `edge://extensions` or `chrome://extensions`, enable developer mode,
   and load this directory as an unpacked extension.
4. Open the extension side panel, set the control-plane URL, grant that origin,
   and pair with the one-time token.

The extension stores only its scoped pairing token. It must not store the
management token.

## Verification

```powershell
node --test browser-extension\pdd-product-extractor\tests\normalizer.test.mjs
node --check browser-extension\pdd-product-extractor\background.js
node --check browser-extension\pdd-product-extractor\content.js
node --check browser-extension\pdd-product-extractor\sidepanel.js
```

## Migration boundary

Reused concepts:

- PDD `window.rawData` as the preferred extraction source.
- Main image, detail image, SKU and specification mapping.
- DOM extraction as an explicitly incomplete fallback.

Removed AutoProcess coupling:

- `localhost:5173` Vue injection and storage keys.
- ports `5000`, `8013` and `8040`.
- placeholder stock values such as `100` or `999`.
- old API polling, duplicate diagnosis scripts and emoji-heavy popup UI.
