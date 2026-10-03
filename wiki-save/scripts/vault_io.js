// Executed by Obsidian eval. The Python caller supplies a base64 JSON payload.
(async () => {
  const request = JSON.parse(new TextDecoder().decode(
    Uint8Array.from(atob('__PAYLOAD__'), c => c.charCodeAt(0))
  ));
  let written = false;
  const encode = value => {
    const bytes = new TextEncoder().encode(JSON.stringify(value));
    let binary = '';
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return 'WIKI_SAVE_RESULT:' + btoa(binary);
  };
  try {
    const vault = app.vault;
    if (vault.getName() !== request.vault ||
        vault.adapter.getBasePath() !== request.vault_path) {
      throw new Error('Vault identity mismatch');
    }
    const path = request.path;
    if (typeof path !== 'string' || !path.endsWith('.md') ||
        path.includes('\\') || /[\x00-\x1f\x7f]/.test(path) ||
        path.split('/').some(part => !part || part.startsWith('.'))) {
      throw new Error('Expected a visible, vault-relative Markdown path');
    }
    // Obsidian Nl also replaces these two spaces before NFC normalization.
    if (/[\u00a0\u202f]/.test(path)) {
      throw new Error('Path contains U+00A0 or U+202F; Obsidian replaces them with U+0020. ' +
        'Confirm and supply the exact space-normalized path before saving');
    }
    if (path.normalize('NFC') !== path) {
      throw new Error('Expected an NFC-normalized path; supply the exact NFC path before saving');
    }
    // Vault.read strips a leading BOM; process callbacks receive raw text.
    const readRaw = file => vault.adapter.read(file.path);
    let file = vault.getFileByPath(path);
    if (request.action === 'snapshot') {
      if (!file) throw new Error('Note does not exist');
      return encode({ok: true, content: await readRaw(file)});
    }
    if (typeof request.content !== 'string') throw new Error('Missing content');
    if (request.action === 'create') {
      if (vault.getAbstractFileByPath(path)) throw new Error('Path already exists');
      written = 'unknown';
      file = await vault.create(path, request.content);
      written = true;
    } else if (request.action === 'update') {
      if (!file) throw new Error('Note does not exist');
      if (typeof vault.process !== 'function') throw new Error('Vault.process is unavailable');
      if (request.content === request.expected) {
        if (await readRaw(file) !== request.expected) throw new Error('Snapshot conflict');
      } else {
        await vault.process(file, current => {
          if (current !== request.expected) throw new Error('Snapshot conflict');
          written = 'unknown';
          return request.content;
        });
        written = true;
      }
    } else {
      throw new Error('Unknown action');
    }
    if (file.path !== path || vault.getFileByPath(path) !== file ||
        await readRaw(file) !== request.content) {
      throw new Error('Readback mismatch');
    }
    return encode({ok: true, written, verified: true, path});
  } catch (error) {
    return encode({ok: false, written, error: String(error.message || error)});
  }
})()
