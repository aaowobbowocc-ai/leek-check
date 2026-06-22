(function () {
  try {
    var hash = window.location.hash || '';
    if (hash.charAt(0) === '#') hash = hash.substring(1);
    var params = new URLSearchParams(hash);
    var at = params.get('access_token');
    var rt = params.get('refresh_token');
    if (at && rt) {
      var expires = new Date(Date.now() + 120 * 1000).toUTCString();
      document.cookie =
        'leek_oauth_at=' + encodeURIComponent(at) +
        '; path=/; expires=' + expires + '; SameSite=Lax';
      document.cookie =
        'leek_oauth_rt=' + encodeURIComponent(rt) +
        '; path=/; expires=' + expires + '; SameSite=Lax';
    }
  } catch (e) {
    console.error('OAuth callback parse failed', e);
  }
  setTimeout(function () {
    window.location.replace('/');
  }, 200);
})();
