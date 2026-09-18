# ETRN Android CryptoPro track — verified state 2026-09-18

## Goal
Batch-sign ETRN in Saby TMS from Android without attaching a physical Rutoken to the phone.

## Exact Saby binary audited
- package: ru.tensor.sbis.courier.saby
- version: 26.3246.5
- versionCode: 510009424
- APK SHA-256: 2cd0acc58555471ad679ce6b4d7a4f3aa0597e3c3b991760873d7851962d5918
- this hash matches the APK previously supplied for the project.

## Verified internal architecture
The exact Saby APK contains two independent crypto-provider features:
- ru.tensor.sbis.cryptopro_config.CryptoProvider.Rutoken
- ru.tensor.sbis.cryptopro_config.CryptoProvider.CryptoPro

CryptoProPlugin creates both RutokenProvider and CryptoProProvider and exposes both providers.

CryptoProProvider:
- initializes embedded CryptoPro CSP via CryptoProInitializer;
- registers/unregisters the Android application context;
- initializes CryptoMobileApi;
- propagates CSP initialization and license state to the common crypto controller.

RutokenProvider is separate and initializes Rutoken RtTransport with physical interfaces, including USB/NFC.

The common CryptoMobileApi exposes:
- createDetachedSign(data, certificateObjId)
- createSignOnHashMass(hashes, certificateObjId)

Native controller contains both:
- GetCryptoProReaders / GetCryptoProContainerPrefix / GetCryptoProProviderType / IsRealCryptoProInstalled
- Rutoken PKCS#11 providers and slot filters
- native_createSignOnHashMass

Therefore batch signing is not structurally tied to Rutoken at the Java API boundary. The decisive object is the certificate object selected by Saby.

## Embedded CryptoPro support
The APK contains CryptoPro JCSP/JCP/CAdES and native libcspjni.
It includes storage/reader infrastructure for CryptoPro software containers and PC/SC devices.
Official CryptoPro Android documentation supports HDIMAGE local containers and PFX import into HDIMAGE.

Official Saby documentation confirms that a mobile-device key container can be managed as "Из КриптоПро" and that Saby supports copying eligible signatures to a mobile device via CryptoPro QR/PFX.

## Important constraint
Existing FNS-issued private keys and private keys stored on embedded-SKZI carriers cannot be copied/exported. This track is not an attempt to extract such a key.

## Main track
Saby TMS mass-selection -> Saby CryptoProProvider -> local Android CryptoPro container -> common createSignOnHashMass -> Saby document completion.

Stock "Rutoken Technologies" emulation is not the main track. Its public Android code loads Rutoken PKCS#11 and physical RtTransport readers; a generic CryptoPro HDIMAGE container does not automatically become a Rutoken device.

## Evidence gate before involving a driver
1. Prove DocumentSignActivity attaches CryptoProProvider for the signing route.
2. Prove a local CryptoPro HDIMAGE/PFX certificate appears to Saby as a selectable certificate object.
3. Prove createSignOnHashMass signs multiple synthetic hashes with that certificate object in an isolated Android lab.
4. Only then solve production issuance of a legally valid IP KEP that can live in a supported mobile key source.
5. Driver field acceptance is last, not part of development.

Status: ANDROID_CRYPTOPRO_TRACK_SELECTED / DRIVER_TESTING_BLOCKED.
