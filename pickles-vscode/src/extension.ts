import * as vscode from 'vscode';
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
} from 'vscode-languageclient/node';

let client: LanguageClient | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const config = vscode.workspace.getConfiguration('pickles');
  const pythonPath = config.get<string>('pythonPath', 'python');
  const serverScript = config.get<string>('serverScript', '');

  if (!serverScript) {
    vscode.window.showWarningMessage(
      'Pickles: set "pickles.serverScript" (in Settings) to the absolute path ' +
        'of pickles_lsp_server.py.'
    );
    return;
  }

  const serverOptions: ServerOptions = {
    command: pythonPath,
    args: [serverScript],
  };

  const clientOptions: LanguageClientOptions = {
    documentSelector: [{ scheme: 'file', language: 'pickles' }],
  };

  client = new LanguageClient(
    'pickles',
    'Pickles Language Server',
    serverOptions,
    clientOptions
  );

  client.start().then(undefined, (err: Error) => {
    vscode.window.showErrorMessage(
      `Pickles: failed to start the language server (${err.message}). ` +
        'Check "pickles.pythonPath" and "pickles.serverScript" in Settings.'
    );
  });
}

export function deactivate(): Thenable<void> | undefined {
  return client?.stop();
}
