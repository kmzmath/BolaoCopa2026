# Bolão Copa 2026

Site mobile-first para bolão entre amigos, com cadastro por usuário/senha, palpites por partida, ranking automático e painel administrativo para informar ou corrigir resultados.

## O que está implementado

- Cadastro e login por nome de usuário e senha.
- Foto de perfil obrigatória na criação de conta.
- Admin criado por variáveis de ambiente.
- Calendário importado de `data/copa_do_mundo_2026_jogos_horario_brasilia.csv`.
- Horários em `America/Sao_Paulo` e bloqueio de palpites no instante de início da partida.
- Palpites editáveis até o bloqueio.
- Final com campeão e vice-campeão, bloqueada no início do primeiro jogo.
- Palpites de outros usuários ocultos até 5 minutos após o início do jogo.
- Pontuação automática ao encerrar ou editar resultado.
- Perfil com foto do jogador.
- Visão de jogos sem palpite e contador de pendências no topo.
- Exportação admin em JSON/CSV com usuários, palpites, resultados, ranking e auditoria.
- Administração de usuários: renomear, resetar senha e ativar/desativar conta.
- Auditoria de resultados com admin responsável, data, placar anterior e placar novo.
- PWA básico para adicionar o site à tela inicial do celular.
- Ranking com posição, foto, jogador, palpites realizados, pontos e estatísticas por tipo de acerto.
- Mata-mata pontuado apenas pelo placar do tempo regulamentar.
- Resolução automática de posições de grupos e de vencedores/perdedores quando houver vencedor no tempo regulamentar.

## Rodar localmente

```bash
python app.py
```

Abra `http://127.0.0.1:8000`.

Por padrão, em desenvolvimento o admin é:

- Usuário: `Math`
- Senha: `admin123`

Defina outras credenciais antes de subir:

```bash
ADMIN_USERNAME=seu_usuario
ADMIN_PASSWORD=sua_senha_forte
SECRET_KEY=um_segredo_longo
python app.py
```

Sem `DATABASE_URL`, o app usa SQLite local em `data/bolao.sqlite3`.

## Deploy no Render

O `render.yaml` cria um Web Service no plano free e espera estas variáveis:

- `SECRET_KEY`: gerada automaticamente pelo Render.
- `ADMIN_USERNAME`: usuário admin inicial.
- `ADMIN_PASSWORD`: senha admin inicial.
- `DATABASE_URL`: URL de conexão Postgres.

Importante: o filesystem do Web Service free do Render é efêmero, então SQLite não deve ser usado em produção. O app agora falha ao iniciar no Render se `DATABASE_URL` não estiver configurada, para evitar perda silenciosa de dados.

A documentação atual do Render informa que:

- Web services free podem dormir após 15 minutos sem tráfego e demorar até cerca de 1 minuto para acordar.
- Web services free não suportam persistent disks.
- Render Postgres free expira 30 dias após a criação e não tem backups.

Como a Copa de 2026 dura mais de 30 dias, use uma destas opções:

- Render Web Service free + Postgres externo persistente, colocando a URL em `DATABASE_URL`.
- Render Web Service free + Render Postgres pago durante o torneio.
- Render Postgres free apenas para testes antes do bolão real.

Checklist para criar no Render:

1. Suba este repositório para o GitHub.
2. No Render, crie um Blueprint a partir do repositório ou um Web Service manual usando:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:application --bind 0.0.0.0:$PORT`
3. Escolha o instance type `Free` para o web service.
4. Configure `ADMIN_PASSWORD`.
5. Configure `DATABASE_URL` com uma URL Postgres persistente.
6. Confirme que `SECRET_KEY` foi gerada ou defina uma manualmente.

Referências:

- https://render.com/docs/free
- https://render.com/docs/disks
- https://render.com/docs/environment-variables

## Testes

```bash
python -m unittest discover -s tests
```

## Observações de regra

Na aba Final, cada jogador escolhe campeão e vice-campeão antes do primeiro jogo da Copa. O acerto do campeão soma 10 pontos, e o vice-campeão correto soma 5 pontos.

O desempate de grupos é calculado por pontos, saldo de gols, gols pró, vitórias e nome do time. Se você quiser espelhar todos os critérios oficiais da FIFA em ordem completa, essa função pode ser refinada em `compute_group_tables`.

Para os placeholders de `3º colocado dos Grupos ...`, o sistema escolhe o melhor terceiro colocado entre os grupos listados quando todos estiverem completos.
