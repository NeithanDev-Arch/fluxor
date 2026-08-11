# Imagens do repositório

Esta pasta guarda as imagens usadas no README. Ela está vazia de propósito —
capturas de tela precisam ser feitas na sua máquina.

## O que capturar (em ordem de impacto)

### 1. `dashboard.png` — a mais importante

```bash
fluxor serve
```

Antes de fotografar, rode alguns workflows para o painel não aparecer vazio:

```bash
fluxor run hello-mundo
fluxor run cotacao-dolar
fluxor run clima-diario
fluxor run github-radar
```

Abra `http://localhost:8000`, deixe a janela em torno de 1400 px de largura e
capture a tela inteira. Se quiser mostrar as duas aparências, faça uma versão
clara e uma escura (o dashboard segue o tema do sistema operacional).

Depois, no README, troque o bloco de instrução por:

```markdown
![Dashboard](docs/dashboard.png)
```

### 2. `execucao.gif` — o que mais prende atenção

Um GIF de 10 a 15 segundos vale mais que dois parágrafos. Sugestão de roteiro:

1. terminal mostrando o arquivo YAML;
2. `fluxor run monitor-preco` com os passos aparecendo um a um;
3. corte para o dashboard, com a execução recém-criada na lista;
4. clique na execução abrindo o painel lateral com o detalhe dos passos.

Ferramentas: [ScreenToGif](https://www.screentogif.com/) (Windows),
[Kap](https://getkap.co/) (macOS), [Peek](https://github.com/phw/peek) (Linux).
Para o terminal isolado, [asciinema](https://asciinema.org/) +
[agg](https://github.com/asciinema/agg) gera um GIF leve e nítido.

Mantenha abaixo de 5 MB — acima disso o GitHub demora a carregar e a pessoa
rola a página antes de ver.

### 3. `detalhe-execucao.png`

O painel lateral aberto, mostrando a saída JSON de cada passo. É o que comunica
"isso é observável de verdade".

## Dicas

- Rode um workflow que falha de propósito (`fluxor run` num YAML com
  `flow.fail`) para o gráfico ter barras vermelhas — um painel 100% verde
  parece mock.
- Esconda dados sensíveis antes de publicar: tokens, e-mails, URLs internas.
- Prefira PNG para telas estáticas e GIF só para movimento.
