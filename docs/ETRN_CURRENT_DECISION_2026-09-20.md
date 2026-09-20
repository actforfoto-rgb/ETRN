# ETRN — current technical decision (2026-09-20)

## Do not delete the research branches

The project is not abandoned. Branches contain reproducible evidence and are deliberately separated from main.

## Closed routes

- Copying/exporting an existing non-exportable FNS UKЭП private key from Rutoken into Android: closed.
- A standalone Android app pretending to stock Saby to be a physical Rutoken: closed as a normal supported architecture.
- FNS "Моя подпись" as the current Saby mass-signing route: closed for this project based on Saby support response.

## Proven facts

- Goskey service 60025907 supports a package of multiple documents; published limit is 20 documents per request.
- Saby 26.3246.5 contains a multi-file crypto API: createSigns(objectId, List<SignedFile>, ...).
- Official Saby sabyCryptoOperation.Create accepts one DocumentID and a Files list of AttachmentID.
- Static APK analysis cannot prove whether AttachmentID values from DIFFERENT ETRN documents are accepted under that single DocumentID.

## Remaining gate

CROSS_DOCUMENT_SERVER_ACCEPTANCE: UNKNOWN.

Question: can one sabyCryptoOperation/Create/external-certificate operation contain signable attachments belonging to different ETRN documents?

## Next route

1. Use Saby's official test API stand if the crypto method is exposed there.
2. Validate two different synthetic/test ETRN documents in one crypto operation.
3. Only if the test stand cannot exercise Goskey, ask Tensor for an explicit contract answer / supported integration test.
4. A production-driver test is the last resort and must be a single controlled two-document acceptance test, not iterative debugging.

## Production release

BLOCKED until the cross-document server gate is proved and result-to-document mapping/finalization is verified.
