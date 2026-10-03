(() => {
  const ETRN_LAST_UPDATED = '03.10.2026';
  const INSTALL_BUTTON_ID = 'pwa-install-button';
  let deferredPrompt = null;

  const isStandalone = () =>
    window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true;

  function removeInstallButton() {
    document.getElementById(INSTALL_BUTTON_ID)?.remove();
  }

  function showInstallButton(mode) {
    if (isStandalone() || document.getElementById(INSTALL_BUTTON_ID)) return;

    const button = document.createElement('button');
    button.id = INSTALL_BUTTON_ID;
    button.type = 'button';
    button.textContent = mode === 'ios' ? 'На экран «Домой»' : 'Установить приложение';
    Object.assign(button.style, {
      position: 'fixed',
      left: '50%',
      transform: 'translateX(-50%)',
      bottom: 'calc(88px + env(safe-area-inset-bottom, 0px))',
      zIndex: '9999',
      border: '1px solid rgba(207,10,29,.16)',
      borderRadius: '12px',
      padding: '10px 14px',
      background: '#fff',
      color: '#b5091a',
      font: '600 14px/1.2 system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
      boxShadow: '0 8px 24px rgba(22,29,37,.14)',
      cursor: 'pointer',
      whiteSpace: 'nowrap'
    });

    button.addEventListener('click', async () => {
      if (mode === 'ios') {
        alert('На iPhone: откройте сайт в Safari → нажмите «Поделиться» → «На экран “Домой”» → «Добавить».');
        return;
      }

      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      try {
        await deferredPrompt.userChoice;
      } finally {
        deferredPrompt = null;
        removeInstallButton();
      }
    });

    document.body.appendChild(button);
  }

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferredPrompt = event;
    showInstallButton('native');
  });

  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    removeInstallButton();
  });

  document.addEventListener('DOMContentLoaded', () => {
    const ua = navigator.userAgent || '';
    const isIOS = /iPad|iPhone|iPod/.test(ua) ||
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

    if (isIOS && !isStandalone()) {
      window.setTimeout(() => showInstallButton('ios'), 1000);
    }
  });

  function syncLastUpdatedDate() {
    const note = document.querySelector('.version-note');
    if (note) note.textContent = 'Материалы актуализированы: ' + ETRN_LAST_UPDATED;
  }

  const appRoot = document.getElementById('app');
  if (appRoot) {
    const observer = new MutationObserver(syncLastUpdatedDate);
    observer.observe(appRoot, { childList: true, subtree: true });
  }
  document.addEventListener('DOMContentLoaded', syncLastUpdatedDate);
  syncLastUpdatedDate();

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', async () => {
      try {
        const registration = await navigator.serviceWorker.register('./service-worker.js', { scope: './' });
        registration.update().catch(() => {});
      } catch (error) {
        console.warn('PWA service worker registration failed:', error);
      }
    });
  }
})();
