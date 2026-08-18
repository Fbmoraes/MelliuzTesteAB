@echo off
REM =========================================================================
REM rodar_analise.bat
REM Preencha as duas linhas abaixo com SUAS credenciais e salve o arquivo.
REM Este arquivo NUNCA deve ir pro GitHub (ja esta no .gitignore).
REM
REM Agora o script pergunta interativamente quais arquivos processar --
REM nao precisa mais editar --file aqui. So rode e siga o menu.
REM =========================================================================

set ANTHROPIC_API_KEY=
set GOOGLE_SERVICE_ACCOUNT_JSON=

REM Troque pelo ID real da sua planilha (parte da URL entre /d/ e /edit)
set SHEET_ID=

python analisar_ab.py --sheet-id %SHEET_ID%

pause
