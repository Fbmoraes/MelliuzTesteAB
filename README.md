# 🚀 Analisador Automatizado de Testes A/B - Méliuz

Este repositório contém uma solução automatizada para analisar testes A/B de cashback. O sistema processa arquivos CSV, calcula métricas de negócio (Lucro Líquido, Ticket Médio e Margem), realiza testes de significância estatística e gera relatórios automatizados. Opcionalmente, utiliza a API do Claude (Anthropic) para escrever um resumo executivo com a recomendação.

## 📌 Links do Projeto
- **Planilha de Acompanhamento (Google Sheets):** [Acessar Planilha de Resultados](https://docs.google.com/spreadsheets/d/1f8FF9c18iUoJjQc9-3Rg9_UccL0MujTW1Ug0vLaO7gA/edit?usp=sharing)
- **Relatórios:** Localizados na pasta `reports` após a execução.

## ⚙️ Passo a Passo: Integração com Google Sheets (Importante)

Para que o script consiga registrar os testes automaticamente no Google Sheets (criando abas detalhadas e preenchendo o resumo), é preciso configurar uma credencial. **ATENÇÃO: A chave JSON gerada nestes passos é um dado sensível. Ela já está ignorada pelo Git e NUNCA deve ser enviada ao GitHub.**

1. **Crie um projeto no Google Cloud Console:** Acesse console.cloud.google.com, faça login com sua conta Google, e crie um projeto novo (ex.: 'meliuz-ab-tool'). Não é necessário cartão de crédito; o uso da API do Sheets é gratuito para este volume.
2. **Ative a API do Google Sheets:** Dentro do projeto, vá em "APIs e serviços" → "Biblioteca", busque por "Google Sheets API" e clique em "Ativar".
3. **Crie uma Service Account:** Vá em "APIs e serviços" → "Credenciais" → "Criar credenciais" → "Conta de serviço". Dê um nome (ex.: 'meliuz-sheets-writer') e conclua sem adicionar papéis especiais.
4. **Gere a chave JSON:** Clique na conta de serviço recém-criada, vá na aba "Chaves" → "Adicionar chave" → "Criar nova chave" → Tipo JSON. O download iniciará automaticamente. Salve este arquivo no seu computador (ex.: `credenciais-google.json`).
5. **Compartilhe a Planilha:** Abra o JSON baixado e copie o valor correspondente a `"client_email"`. Abra a planilha do projeto no navegador, clique em "Compartilhar" e dê permissão de **Editor** para este e-mail.

## 🚀 Como Rodar o Projeto (Windows)

### 1. Pré-requisitos
- Python 3.8+ instalado.
- Instale as bibliotecas necessárias abrindo o terminal no projeto e executando:

    pip install pandas numpy scipy gspread google-auth anthropic

### 2. Configurando o ambiente
Abra o arquivo `rodar_analise.bat` e preencha as variáveis correspondentes:
- `ANTHROPIC_API_KEY`: Insira sua chave da Anthropic/Claude (se deixar em branco, o script usa um gerador de texto local padrão).
- `GOOGLE_SERVICE_ACCOUNT_JSON`: O caminho no seu computador para o arquivo JSON baixado no Passo 4.
- `SHEET_ID`: O ID da planilha. Utilize `1f8FF9c18iUoJjQc9-3Rg9_UccL0MujTW1Ug0vLaO7gA`.

### 3. Execução Interativa
Execute o arquivo `rodar_analise.bat` dando um duplo clique. 
O script mapeará todos os arquivos `.csv` na pasta e abrirá um menu interativo no terminal. Basta digitar o número do dataset, informar o nome e a descrição quando solicitado, e aguardar a análise.

## 💻 Como Rodar o Projeto (Linux / Mac / Terminal direto)

Se preferir rodar direto via linha de comando, exporte as variáveis no seu terminal:

    export ANTHROPIC_API_KEY="sua_chave_aqui"
    export GOOGLE_SERVICE_ACCOUNT_JSON="/caminho/para/credenciais-google.json"

E rode o script Python utilizando o menu interativo:

    python analisar_ab.py --sheet-id 1f8FF9c18iUoJjQc9-3Rg9_UccL0MujTW1Ug0vLaO7gA

Se desejar automatizar, você pode passar os argumentos diretamente:

    python analisar_ab.py --file parceiro_A.csv --nome "Teste Parceiro A" --descricao "3 Variantes testadas" --sheet-id 1f8FF9c18iUoJjQc9-3Rg9_UccL0MujTW1Ug0vLaO7gA
