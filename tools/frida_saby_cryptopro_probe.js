'use strict';

function emit(tag, value) {
  send({tag: tag, value: String(value)});
}

function probe() {
  Java.perform(function () {
    try {
      var ActivityThread = Java.use('android.app.ActivityThread');
      var app = ActivityThread.currentApplication();
      emit('APP', app ? app.getPackageName().toString() : 'NULL');
      if (!app) return;

      var Init = Java.use('ru.tensor.sbis.cryptopro_config.CryptoProInitializer');
      var initializer = Init.$new();
      var initResult = initializer.init(app);
      emit('CRYPTOPRO_INIT_RESULT', initResult);
      try { emit('CRYPTOPRO_INIT_STATE', initializer.getInitializationState()); } catch (e) { emit('INIT_STATE_ERROR', e); }

      var Security = Java.use('java.security.Security');
      var providers = Security.getProviders();
      var names = [];
      for (var i = 0; i < providers.length; i++) names.push(providers[i].getName().toString());
      emit('SECURITY_PROVIDERS', names.join(','));

      var KeyStore = Java.use('java.security.KeyStore');
      var candidates = [
        ['HDIMAGE', 'JCSP'],
        ['HDImageStore', 'JCSP'],
        ['HDIMAGE', null],
        ['HDImageStore', null]
      ];
      for (var c = 0; c < candidates.length; c++) {
        var type = candidates[c][0], provider = candidates[c][1];
        try {
          var ks = provider ? KeyStore.getInstance(type, provider) : KeyStore.getInstance(type);
          ks.load(null, null);
          var en = ks.aliases(), aliases = [];
          while (en.hasMoreElements()) aliases.push(String(en.nextElement()));
          emit('KEYSTORE_OK', type + '|' + provider + '|aliases=' + aliases.length + '|' + aliases.join(','));
        } catch (e) {
          emit('KEYSTORE_FAIL', type + '|' + provider + '|' + e);
        }
      }

      try {
        var Cpp = Java.use('ru.tensor.sbis.crypto.generated.CryptoMobileApi$CppProxy');
        var methods = Cpp.class.getDeclaredMethods();
        var selected = [];
        for (var m = 0; m < methods.length; m++) {
          var s = methods[m].toString();
          if (s.indexOf('createSignOnHashMass') >= 0 ||
              s.indexOf('GetCryptoProReaders') >= 0 ||
              s.indexOf('GetCryptoProContainerPrefix') >= 0 ||
              s.indexOf('IsRealCryptoProInstalled') >= 0) selected.push(s);
        }
        emit('CRYPTO_MOBILE_METHODS', selected.join(' || '));
      } catch (e) {
        emit('CRYPTO_MOBILE_REFLECTION_ERROR', e);
      }

      emit('PROBE_DONE', 'YES');
    } catch (e) {
      emit('FATAL', e.stack || e);
    }
  });
}

setTimeout(probe, 5000);
