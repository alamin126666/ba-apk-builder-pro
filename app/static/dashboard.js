(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const form = document.querySelector('#build-form');
  const webInput = document.querySelector('#web-project');
  const logoInput = document.querySelector('#logo');
  const dropZone = document.querySelector('#drop-zone');
  const activeBuild = document.querySelector('#active-build');
  const emptyState = document.querySelector('#empty-state');
  const buildButton = document.querySelector('#build-button');
  let selectedBuildId = null;
  let pollTimer = null;
  let pollDelay = 1300;
  const activeBuildKey = 'apk-builder-active-build';

  async function jsonResponse(response) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) { window.location.assign('/login'); throw new Error('Your session expired. Sign in again.'); }
    if (!response.ok) throw new Error(data.detail || 'The request could not be completed.');
    return data;
  }

  function chooseProject(files) {
    const file = files?.[0];
    if (!file) return;
    if (!/\.(html?|zip)$/i.test(file.name)) { showNotice('Choose a .html file or a .zip web project.'); return; }
    const chip = document.querySelector('#project-file');
    chip.textContent = file.name;
    chip.hidden = false;
    document.querySelector('#drop-title').textContent = 'Project selected';
    webInput.files = files;
  }

  function showNotice(message) {
    const box = document.querySelector('#error-box');
    document.querySelector('#result-box').hidden = true;
    document.querySelector('#cancel-button').hidden = true;
    document.querySelector('#error-message').textContent = message;
    document.querySelector('#error-box strong').textContent = 'Check your details';
    box.hidden = false;
    activeBuild.hidden = false;
    emptyState.hidden = true;
    document.querySelector('#status-title').textContent = 'Build needs attention';
    setStatus('failed');
  }

  function showConnectionNotice() {
    const box = document.querySelector('#error-box');
    document.querySelector('#result-box').hidden = true;
    document.querySelector('#error-box strong').textContent = 'Connection interrupted';
    document.querySelector('#error-message').textContent = 'The build may still be running. Reconnecting to its status…';
    box.classList.add('is-warning');
    box.hidden = false;
    document.querySelector('#cancel-button').hidden = false;
    document.querySelector('#status-title').textContent = 'Reconnecting to your build';
    setStatus('building');
  }

  function schedulePoll(buildId, delay = pollDelay) {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(() => poll(buildId), delay);
  }

  function startPolling(buildId) {
    selectedBuildId = buildId;
    pollDelay = 1300;
    sessionStorage.setItem(activeBuildKey, buildId);
    buildButton.disabled = true;
    buildButton.querySelector('.button-label').textContent = 'Build in progress…';
    poll(buildId);
  }

  dropZone?.addEventListener('click', () => webInput.click());
  webInput?.addEventListener('change', () => chooseProject(webInput.files));
  for (const eventName of ['dragenter', 'dragover']) dropZone?.addEventListener(eventName, event => { event.preventDefault(); dropZone.classList.add('is-dragging'); });
  for (const eventName of ['dragleave', 'drop']) dropZone?.addEventListener(eventName, event => { event.preventDefault(); dropZone.classList.remove('is-dragging'); });
  dropZone?.addEventListener('drop', event => {
    const files = event.dataTransfer?.files;
    if (!files?.length) return;
    try { webInput.files = files; } catch (_) { /* A click can still select the same file on browsers with a read-only FileList. */ }
    chooseProject(files);
  });
  logoInput?.addEventListener('change', () => {
    const file = logoInput.files?.[0];
    if (!file) return;
    if (!/^image\/(png|jpeg|webp)$/i.test(file.type) && !/\.(png|jpe?g|webp)$/i.test(file.name)) { showNotice('Use a PNG, JPG, or WEBP app icon.'); logoInput.value = ''; return; }
    document.querySelector('#logo-name').textContent = file.name;
    const reader = new FileReader();
    reader.onload = () => { document.querySelector('#icon-preview').replaceChildren(Object.assign(new Image(), { src: reader.result, alt: 'Selected app icon' })); };
    reader.readAsDataURL(file);
  });

  function setStatus(status) {
    const pill = document.querySelector('#status-pill');
    pill.className = `status-pill status-${status}`;
    pill.textContent = status.toUpperCase();
  }

  function renderBuild(build) {
    emptyState.hidden = true;
    activeBuild.hidden = false;
    document.querySelector('#status-title').textContent = build.status === 'success' ? 'APK is ready' : build.status === 'failed' ? 'Build needs attention' : build.status === 'cancelled' ? 'Build cancelled' : 'Building your app';
    document.querySelector('#summary-name').textContent = build.app_name;
    document.querySelector('#summary-package').textContent = build.package_name;
    document.querySelector('#stage-label').textContent = build.stage;
    document.querySelector('#progress-value').textContent = `${build.progress}%`;
    const track = document.querySelector('#progress-track');
    track.value = build.progress;
    track.classList.toggle('is-working', build.status === 'building');
    setStatus(build.status);
    const list = document.querySelector('#build-steps');
    list.replaceChildren(...(build.steps || []).map(step => {
      const item = document.createElement('li');
      item.className = step.state;
      item.textContent = step.label;
      return item;
    }));
    const success = build.status === 'success' && !!build.download_url;
    document.querySelector('#result-box').hidden = !success;
    if (success) document.querySelector('#download-link').href = build.download_url;
    const errorBox = document.querySelector('#error-box');
    errorBox.classList.remove('is-warning');
    errorBox.hidden = build.status !== 'failed' && build.status !== 'cancelled';
    document.querySelector('#error-box strong').textContent = build.status === 'cancelled' ? 'Build cancelled' : 'Build failed';
    document.querySelector('#error-message').textContent = build.error || '';
    document.querySelector('#cancel-button').hidden = build.status !== 'queued' && build.status !== 'building';
    if (build.status === 'success' || build.status === 'failed' || build.status === 'cancelled') {
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = null;
      sessionStorage.removeItem(activeBuildKey);
      buildButton.disabled = false;
      buildButton.querySelector('.button-label').textContent = 'Build another APK';
      refreshHistory();
    }
  }

  async function refreshHistory() {
    const host = document.querySelector('#history-list');
    if (!host) return;
    try {
      const data = await jsonResponse(await fetch('/api/builds', { headers: { 'Accept': 'application/json' } }));
      host.replaceChildren();
      if (!data.builds.length) { host.innerHTML = '<div class="history-empty">Your completed and recent builds will show up here.</div>'; return; }
      for (const build of data.builds) {
        const row = document.createElement('div'); row.className = 'history-row';
        const app = document.createElement('div'); app.className = 'history-app';
        const name = document.createElement('strong'); name.textContent = build.app_name;
        const pkg = document.createElement('span'); pkg.textContent = build.package_name;
        app.append(name, pkg);
        const date = document.createElement('span'); date.className = 'history-date'; date.textContent = new Date(build.created_at).toLocaleString();
        const status = document.createElement('span'); status.className = `status-pill history-status status-${build.status}`; status.textContent = build.status.toUpperCase();
        row.append(app, date, status);
        if (build.download_url) { const link = document.createElement('a'); link.className = 'history-download'; link.href = build.download_url; link.textContent = 'Download APK ↓'; row.append(link); }
        else { const spacer = document.createElement('span'); row.append(spacer); }
        host.append(row);
      }
      const activeStatuses = new Set(['queued', 'building']);
      const rememberedId = sessionStorage.getItem(activeBuildKey);
      const activeBuildInfo = data.builds.find(build => build.id === rememberedId && activeStatuses.has(build.status))
        || data.builds.find(build => activeStatuses.has(build.status));
      if (!selectedBuildId && activeBuildInfo) {
        document.querySelector('#active-build').hidden = false;
        document.querySelector('#empty-state').hidden = true;
        startPolling(activeBuildInfo.id);
      }
    } catch (_) { /* A history refresh must not interrupt an active build. */ }
  }

  async function poll(buildId) {
    try {
      const data = await jsonResponse(await fetch(`/api/build/${encodeURIComponent(buildId)}/status`, { headers: { 'Accept': 'application/json' }, cache: 'no-store' }));
      pollDelay = 1300;
      renderBuild(data.build);
      if (data.build.status === 'success' || data.build.status === 'failed' || data.build.status === 'cancelled') return;
      schedulePoll(buildId);
    } catch (error) {
      if (error.message === 'Your session expired. Sign in again.') return;
      showConnectionNotice();
      pollDelay = Math.min(pollDelay * 2, 10000);
      schedulePoll(buildId, pollDelay);
    }
  }

  form?.addEventListener('submit', async event => {
    event.preventDefault();
    if (!webInput.files?.length) { showNotice('Choose an HTML file or ZIP project first.'); return; }
    if (!form.reportValidity()) return;
    const data = new FormData(form);
    buildButton.disabled = true;
    buildButton.querySelector('.button-label').textContent = 'Sending project…';
    document.querySelector('#error-box').hidden = true;
    document.querySelector('#result-box').hidden = true;
    try {
      const result = await jsonResponse(await fetch('/api/build', { method: 'POST', headers: { 'X-CSRF-Token': csrf }, body: data }));
      renderBuild(result.build);
      startPolling(result.build.id);
      await refreshHistory();
    } catch (error) {
      buildButton.disabled = false;
      buildButton.querySelector('.button-label').textContent = 'Build APK';
      showNotice(error.message);
    }
  });

  document.querySelector('#cancel-button')?.addEventListener('click', async () => {
    if (!selectedBuildId) return;
    try {
      const result = await jsonResponse(await fetch(`/api/build/${encodeURIComponent(selectedBuildId)}/cancel`, { method: 'POST', headers: { 'X-CSRF-Token': csrf } }));
      renderBuild(result.build);
    } catch (error) { showNotice(error.message); }
  });

  document.querySelector('#logout-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    await fetch('/logout', { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
    window.location.assign('/login');
  });

  refreshHistory();
})();
