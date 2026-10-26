# Contratos internos do api-tcc (para quem implementa)

Leia junto com `docs/plano_api.md`. Aqui está a interface exata de cada módulo. Não mude assinaturas sem combinar.
Python 3.12, código e mensagens em português, sem acentos em nomes de identificadores. Datas sempre como
`str` ISO `"AAAA-MM-DD"` dentro do código (converter `datetime.date` do pymysql com `.isoformat()`).

## Regras que valem para todo o código
- **Nunca abrir, ler, listar ou imprimir nenhum `.env`.** O código lê via `python-dotenv`, e você não.
- Escrever arquivos só dentro de `api-tcc/`. O processamento-tcc é **só leitura** (`saida/`, `saida_completa/`).
- Todo SQL passa por `Conexao` (comum/banco.py), que chama `guarda_sql.verificar`. Por isso o SQL do projeto:
  - usa só SELECT / INSERT INTO / UPDATE / CREATE TABLE IF NOT EXISTS;
  - nunca usa `apelido.coluna`, nunca `FROM a, b`, nunca aspas duplas, `@`, DELETE, DROP, REPLACE,
    EXTRACT(.. FROM ..);
  - nome de tabela sempre via `conexao.t("base")`;
  - valores sempre como parâmetros `%s` (nunca f-string com valor);
  - `INSERT ... ON DUPLICATE KEY UPDATE col = VALUES(col)` é permitido. `AS novo` + `novo.col` NÃO (a guarda
    recusa nome qualificado).
- Colunas JSON: gravar `json.dumps(obj, ensure_ascii=False)`; ao ler, pymysql devolve `str` → `banco.ler_json(v)`.

## comum/tabelas.py (pronto)
`PREFIXO_REAL=""`, `PREFIXO_TESTE="teste_"`, `NOMES_BASE` (14), `FOTOS`, `SERIES`, `VERSIONADAS`, `APOIO`,
`validar_prefixo(p)`, `nome(base, p)`, `nomes(p)`.

## comum/guarda_sql.py (pronto)
`BANCO_PERMITIDO="tcc_processador"`, `SQLRecusado`, `verificar(sql, prefixo, somente_leitura=False)`,
`sql_apagar_tabela_de_teste(nome, prefixo)`.

## comum/config.py
```python
RAIZ: Path                      # pasta api-tcc (Path(__file__).resolve().parent.parent)
class ErroConfig(Exception)
@dataclass(frozen=True) class Credenciais: usuario: str; senha: str = field(repr=False)
@dataclass(frozen=True) class Config:
    host: str; porta: int; banco: str
    escrita: Credenciais; leitura: Credenciais
    prefixo: str                     # "" ou "teste_"
    dir_processamento: Path
    chaves_api: tuple[str, ...]
    cors_origens: tuple[str, ...]
def carregar(ambiente: Mapping[str, str] | None = None) -> Config
```
- `ambiente=None` → `load_dotenv(RAIZ / ".env", override=False)` e usa `os.environ`. Com `ambiente` dado, usa só ele
  (sem dotenv).
- `NOME_BANCO` tem que ser **exatamente** `guarda_sql.BANCO_PERMITIDO`; senão `ErroConfig`. A mensagem não traz
  nenhuma senha.
- `IP_BANCO` obrigatório; `PORTA_BANCO` padrão 3306.
- `escrita`: `USER_ESCRITA`/`SENHA_ESCRITA` se os dois estiverem preenchidos, senão `USER_BANCO`/`SENHA_BANCO`.
  `leitura`: idem com `USER_LEITURA`/`SENHA_LEITURA`. Usuário final vazio → `ErroConfig`.
- `PROC_API_PREFIXO_TABELAS` (padrão "") validado por `tabelas.validar_prefixo` → inválido = `ErroConfig`.
- `PROC_DIR_PROCESSAMENTO` padrão `RAIZ.parent / "processamento-tcc"`.
- `PROC_API_CHAVES` e `PROC_API_CORS_ORIGENS`: separados por vírgula, com `strip`, sem vazios.

## comum/banco.py
```python
class ErroBanco(Exception)
OBSERVADORES: list[Callable[[str], None]] = []   # cada SQL executado é passado a eles (teste de espionagem)
def ler_json(v) -> Any                            # str/bytes → json.loads; None → None; outro → v
class Conexao:
    prefixo: str; somente_leitura: bool
    def __init__(self, bruta, prefixo: str, somente_leitura: bool)
    def t(self, base: str) -> str                 # tabelas.nome(base, self.prefixo)
    def consultar(self, sql, params=None) -> list[dict]
    def executar(self, sql, params=None) -> int   # rowcount
    def inserir(self, sql, params=None) -> int    # lastrowid
    def executar_muitos(self, sql, lista_params) -> int
    def iniciar(self); def confirmar(self); def desfazer(self); def fechar(self)
    def apagar_tabelas_de_teste(self) -> None     # para cada base: guarda_sql.sql_apagar_tabela_de_teste(self.t(base), self.prefixo)
    __enter__/__exit__ (fecha)
def conectar(cfg: Config, perfil: str) -> Conexao   # perfil "escrita" | "leitura"
```
- Todo `consultar/executar/inserir/executar_muitos` chama `guarda_sql.verificar(sql, self.prefixo, self.somente_leitura)`,
  avisa os OBSERVADORES e só depois executa.
- `params=None` → `cursor.execute(sql)` sem interpolação; com params → `cursor.execute(sql, params)`.
- pymysql: `database=cfg.banco`, `charset="utf8mb4"`, `autocommit=False`, `cursorclass=DictCursor`,
  `connect_timeout=10`. Perfil leitura: `init_command="SET SESSION TRANSACTION READ ONLY"` e `somente_leitura=True`.
- Logo depois de conectar: `SELECT DATABASE() AS banco` precisa dar `tcc_processador`; senão fecha e levanta
  `ErroBanco`.
- Erros do pymysql viram `ErroBanco(f"... (código MySQL {codigo})")`, sem usuário nem senha.

## comum/contrato.py
```python
VERSAO = "1.0"
STATUS_VALIDOS = frozenset({"ok","nao_se_aplica","indisponivel","historico_insuficiente","lucro_negativo"})
STATUS_EXTRAS = {"experimento_top": frozenset({"calibracao_nao_conferida"})}
STATUS_EXECUCAO = frozenset({"em_andamento","ok","parcial","falhou"})
TIPOS_SERIE = ("acao","etf","indice","taxa","setor_sem_empresa")
METADADOS = ("versao_contrato","gerado_em","dados_ate")
CAMPOS_ENVELOPE = ("pacote","versao_contrato","chave","gerado_em","dados_ate","registros","removidos")
@dataclass(frozen=True) class Pacote: nome: str; chave: tuple[str, ...]; familia: str  # "foto"|"serie"|"versionada"
PACOTES: dict[str, Pacote] = {
  "acoes": ("ticker",) versionada, "mercado_dia": ("data_referencia",) foto,
  "resumo_acao": ("data_referencia","ticker") foto, "series_diarias": ("ticker","data") serie,
  "fundamentos_trimestrais": ("ticker","data_fim_periodo") versionada,
  "proventos": ("ticker","tipo","data_com") versionada, "noticias_dia": ("data_referencia","ticker") foto,
  "experimento_top": ("data_referencia",) foto, "experimento_historico": ("data_referencia",) foto,
  "execucao": ("data_referencia","rodada") foto }
def json_canonico(obj) -> str      # json.dumps(sort_keys=True, ensure_ascii=False, separators=(",",":"), allow_nan=False)
def sha256_texto(texto: str) -> str
def hash_registro(registro: dict) -> str   # sha256 do json_canonico do registro SEM as chaves METADADOS (só no 1º nível)
def chave_de(pacote: str, registro: dict) -> tuple   # valores dos campos da chave, na ordem; não vale para series_diarias
```
A tabela de cada pacote tem o mesmo nome do pacote, exceto `series_diarias` → `series_pontos` (+ catálogo e ajustes).

## comum/series.py
```python
JANELAS = {"1m": 21, "3m": 63, "6m": 126, "1a": 253, "2a": 506, "max": None}
PREGOES_COMPLETA = 253
@dataclass(frozen=True) class Ponto:
    data: str
    valores: dict[str, float | int | None]   # todos os campos da série
    status: dict[str, str]                   # só campos não-ok neste ponto; {} se tudo ok
def compacto_para_pontos(serie: dict) -> list[Ponto]
def pontos_para_compacto(pontos: list[Ponto], campos: list[str]) -> tuple[list[str], dict[str, list], dict[str, list[dict]]]
def hash_ponto(p: Ponto) -> str              # contrato.sha256_texto(contrato.json_canonico({"valores": p.valores, "status": p.status}))
def montar_registro(meta: dict, catalogo: dict, pontos: list[Ponto], janela: str,
                    recusas: dict[str, str] | None = None, *, inicio_janela: str,
                    inicio_ultima_completa: str | None = None) -> dict
# ATUALIZAÇÃO 26/10/2026: o recorte é "data >= inicio_janela" (início da janela no calendário de pregões,
# calculado pela rota) e não mais "últimos N pontos"; recortar() foi removida. Ver plano, "inicio_janela".
```
- `compacto_para_pontos`: um Ponto por data. `status` de cada trecho `{de, ate, status}` vale para as datas com
  de ≤ data ≤ ate.
- `pontos_para_compacto`: devolve `datas`, `valores` (uma lista por campo, na ordem `campos`) e `status`. O
  `status` tem só os campos com algum ponto não-ok, na ordem `campos`; cada campo vira uma lista de trechos
  **máximos** de datas consecutivas (na lista de pontos) com o mesmo status, `{"de","ate","status"}`.
  Ida e volta com a série real tem que dar exatamente a série original (os trechos reais já são máximos).
- `montar_registro(meta, catalogo, pontos, janela, recusas)`:
  - `meta` = `{versao_contrato, gerado_em, dados_ate}`; `catalogo` = `{ticker, tipo, nome, campos}`; `pontos` =
    todos os disponíveis até a data final, em ordem crescente.
  - `recusas` = `{campo: data_ate}`: nesses campos, pontos com data ≤ data_ate saem com valor `None` e status
    `"indisponivel"`. Aplicar **antes** de compactar.
  - O registro sai com `versao_contrato, gerado_em, dados_ate, ticker, tipo, nome, substituir_serie=True,
    inicio_janela=<1ª data do recorte>, datas, valores, status`.
  - Com `janela` em ("2a","max"), acrescenta `inicio_ultima_completa` = data do 253º ponto contado do fim (ou o
    1º, se houver menos).
  - Lista vazia → `ValueError`.

## publicador/entrada.py (estruturas compartilhadas)
```python
@dataclass class Entrada:
    data: str                                # D
    execucao: dict                           # envelope de saida/D/execucao.json (obrigatório)
    completa: dict[str, dict] | None         # pacote -> envelope (só os presentes, sem execucao); None se não há completa
    execucao_completa: dict | None           # envelope de saida_completa/D/execucao.json
    hashes_arquivos: dict[str, str]          # "saida/D/execucao.json" -> sha256 dos bytes
    pasta_completa: str | None; pasta_saida: str
```

## publicador/leitor.py
```python
class ErroEntrada(Exception)
def ler(dir_processamento: Path, data: str) -> Entrada
```
- `data` no formato AAAA-MM-DD (senão ErroEntrada).
- `saida/D/execucao.json` obrigatório (senão `ErroEntrada("nada para publicar: ...")`).
- Se `saida_completa/D/` existe: lê cada `<pacote>.json` de `contrato.PACOTES` (menos execucao) que existir; o
  `execucao.json` da completa vai para `execucao_completa`. Se não existir nenhum pacote → `completa=None`.
- Abre os arquivos em modo binário: hash dos bytes + `json.loads(bytes.decode("utf-8"))`. Nunca escreve nada.

## publicador/validador.py
```python
def validar(entrada: Entrada) -> list[str]     # lista de erros legíveis; vazia = ok
```
- **execucao:** envelope completo (`CAMPOS_ENVELOPE`), `pacote == "execucao"`, versão 1.0, registros não vazios;
  cada registro com `data_referencia == D`, `rodada` int, `status` em `STATUS_EXECUCAO`.
- **Cada pacote da completa:**
  - envelope completo, `pacote` == nome do arquivo, `versao_contrato == "1.0"`;
  - `chave == list(PACOTES[p].chave)`, `registros` lista, `removidos` lista;
  - cada registro com `versao_contrato == "1.0"`;
  - campos da chave presentes e não nulos (séries: `ticker`); datas da chave em AAAA-MM-DD;
  - chave única no pacote.
- **Folhas (todos os pacotes, menos series_diarias):** todo dict que tenha as chaves `valor` E `status` precisa
  cumprir:
  - status em STATUS_VALIDOS (+ STATUS_EXTRAS do pacote);
  - `ok` → valor não nulo;
  - valor nulo → status ≠ ok;
  - valor não nulo com status ≠ ok → só `lucro_negativo` (e o extra do pacote).
  Percorrer recursivamente. Dict sem `valor` é ignorado (ex.: itens de notícia).
- **series_diarias:**
  - `tipo` em TIPOS_SERIE;
  - `datas` estritamente crescentes no formato ISO;
  - cada lista de `valores` do tamanho de `datas`, com número (não bool) ou None;
  - `status`: dict campo → lista de `{de, ate, status}`, com status válido, de ≤ ate, de/ate presentes em
    `datas`, sem sobreposição;
  - cada `null` coberto por um trecho; valor coberto por trecho só com `lucro_negativo`.
- **Cruzada:** `execucao_completa.registros[0].pacotes_enviados` é lista de `{pacote, arquivo, registros,
  removidos}`. Para cada item cujo pacote existe na completa, `registros` == len(envelope.registros). Item
  listado sem arquivo → erro.
- `resumo_acao`, `noticias_dia` e `mercado_dia` presentes têm todos a mesma `data_referencia` (senão erro).
- Mensagens: `"resumo_acao[PETR4].pl: status 'ok' com valor nulo"` (pacote + chave + caminho).

## publicador/emenda.py (lógica pura)
```python
TOLERANCIA = 1e-6; FATOR_MIN = 0.01; FATOR_MAX = 100.0
PONTOS_EMENDA = 5; BUSCA_EMENDA = 21; MINIMO_PONTOS = 3
CAMPOS_PROPORCIONAIS = {"acao": ("fechamento","volume","indice_retorno_total"),
                        "etf": ("fechamento","volume","indice_retorno_total"),
                        "indice": ("indice_retorno_total",), "taxa": ("indice_retorno_total",),
                        "setor_sem_empresa": ("indice_retorno_total",)}
CAMPOS_SO_AVISO = {"indice": ("fechamento",)}
@dataclass(frozen=True) class Ajuste:
    campo: str; fator: float | None; de: str | None; ate: str | None; pontos: int
    origem: str          # "emenda" | "janela"
    classificacao: str   # "uniforme" | "evento" | "irregular" | "recusado"
    aplicado: bool; detalhe: dict
@dataclass class ResultadoEmenda:
    ajustes: list[Ajuste]
    fatores_aplicar: dict[str, float]        # campo -> k a multiplicar nos antigos (data < data_corte)
    data_corte: str                          # d0
    datas_explicadas: dict[str, set[str]]    # campo -> datas da janela cuja mudança nesse campo o fator explica
    avisos: list[str]
def analisar(ticker: str, tipo: str, antigos: dict[str, Ponto], novos: list[Ponto]) -> ResultadoEmenda
```
Algoritmo exato no plano (§ "Séries: emenda"). Detalhes:
- `novos` em ordem crescente; d0 = novos[0].data. "Antigos" = `antigos` com data < d0. Sobreposição = datas de
  `novos` que estão em `antigos`, na ordem de `novos`.
- Para cada campo em PROPORCIONAIS[tipo] + SO_AVISO[tipo]: razões `(data, novo/antigo)` na sobreposição, só onde
  os dois valores não são None e antigo != 0.
- `iguais(a, b) = abs(a - b) <= TOLERANCIA * abs(b)`.
- `costura` = razões cujas datas estão entre as `BUSCA_EMENDA` primeiras da sobreposição; pegar as primeiras
  `PONTOS_EMENDA`.
- `alguma_mudanca` = alguma razão não `iguais(r, 1.0)`.
- Sem `alguma_mudanca` → nada para o campo.
- `len(costura) < MINIMO_PONTOS`, ou costura não concorda (todas `iguais(r, k)` com k = costura[0]), ou k fora de
  [FATOR_MIN, FATOR_MAX] → **recusado**, só para campo PROPORCIONAL. Em SO_AVISO, nunca recusado: só um aviso.
- k concordante e `iguais(k, 1.0)` → mudança só no meio da janela: nada de ajuste, nada explicado (vai ponto a
  ponto).
- Classificação:
  - `uniforme`: todas as razões ≈ k;
  - `evento`: existe s (0 < s < len) com razões[:s] ≈ k e razões[s:] ≈ 1; detalhe["data_evento"] = data de
    razões[s];
  - senão `irregular`.
- `n_antigos` = antigos (data < d0) com valor não None no campo.
- **PROPORCIONAL aplicado:**
  - se `n_antigos > 0`: `fatores_aplicar[campo] = k`;
  - `datas_explicadas[campo]` = datas com r ≈ k (uniforme: todas; evento: as anteriores a S; irregular: vazio);
  - 1 Ajuste: `origem` = "emenda" se n_antigos > 0 senão "janela"; `fator=k`; `de` = 1º antigo (ou 1ª data
    explicada); `ate` = última data explicada (ou último antigo se não houver explicada);
    `pontos = n_antigos + len(explicadas)`; `aplicado=True`;
  - `detalhe = {"antigos": {"de","ate","pontos"}, "janela": {"de","ate","pontos"}, "razoes_costura": [...],
    "data_evento"?: S}`.
- **PROPORCIONAL recusado:**
  - Ajuste com `classificacao="recusado"`, `aplicado=False`, `fator=k` (ou None);
  - `de` = 1º antigo, `ate` = último antigo (data < d0), `pontos=n_antigos`, origem "emenda";
  - `detalhe={"motivo": ..., "razoes_costura": [...]}`; aviso
    `"série PETR4, campo fechamento: emenda recusada (fator 141012.1) — pontos antigos ficam indisponíveis na API"`;
  - nada em `fatores_aplicar` nem em `datas_explicadas`;
  - se `n_antigos == 0`, não gera Ajuste (não há o que esconder).
- **SO_AVISO com k ≠ 1:** Ajuste com a classificação geométrica (ou "irregular" se a costura não concorda),
  `aplicado=False`, `detalhe["motivo"]="campo oficial: só aviso"`, mais um aviso; nunca entra em fatores/explicadas.
- Ordem dos ajustes: a ordem dos campos.

## publicador/planejador.py (lógica pura)
**Atualização:** `Versao`, `Estado`, as operações e `Plano` (com `contagens()` pronto) moraram para
**`publicador/operacoes.py`** (pronto; importe de lá). `Estado` não tem método `aplicar`: a aplicação em memória é
`planejador.aplicar_em_memoria(estado, plano) -> None` (ids novos = max + 1; usado nos testes de vários dias).
`Estado()` sem argumentos = estado vazio.
```python
class ErroPublicacao(Exception)
@dataclass class Versao: id: int | None; chave: tuple; valido_desde: str; valido_ate: str | None; hash: str; registro: dict
@dataclass class Estado:
    publicacoes: dict[str, str]                         # D -> status ("completa"|"so_execucao"|"rejeitada")
    fotos: dict[str, dict[tuple, tuple[str, dict]]]     # pacote -> chave -> (hash, registro)
    pontos: dict[str, dict[str, Ponto]]                 # ticker -> data -> Ponto (só não removidos)
    catalogo: dict[str, dict]                           # ticker -> {tipo, nome, campos}
    versoes: dict[str, dict[tuple, list[Versao]]]       # pacote -> chave -> versões em ordem de valido_desde
    recusas: dict[str, dict[str, str]]                  # ticker -> campo -> ate (recusa mais recente) [para a API em memória]
    def aplicar(self, plano: "Plano") -> None           # aplica as operações em memória (ids novos = max+1)
    @staticmethod
    def vazio() -> "Estado"
# Operações (frozen dataclasses). `chave` sempre dict campo->valor (na ordem da chave do contrato).
FotoInserir(pacote, chave, hash, registro)
FotoAtualizar(pacote, chave, hash, registro, hash_anterior, registro_anterior)            # + linha em correcoes
PontoInserir(ticker, ponto, hash)
PontoAtualizar(ticker, ponto, hash, anterior: Ponto | None, hash_anterior: str)           # anterior None = sem correcoes
PontoRemover(ticker, data, anterior: Ponto, hash_anterior)                                # removido_em = D + correcoes
AjusteRegistrar(ticker, ajuste: Ajuste)
CatalogoGravar(ticker, tipo, nome, campos: list[str])
VersaoInserir(pacote, chave, valido_desde, valido_ate, hash, registro)
VersaoFechar(pacote, id, chave, valido_ate)
VersaoCorrigir(pacote, id, chave, hash, registro, hash_anterior, registro_anterior)       # + correcoes
VersaoReabrir(pacote, id, chave)                                                           # valido_ate = NULL
VersaoAntecipar(pacote, id, chave, valido_desde)                                           # modo buraco
@dataclass class Plano:
    data: str; modo: str            # "normal" | "republicacao" | "buraco" | "so_execucao"
    status_publicacao: str          # "completa" | "so_execucao"
    data_referencia: str | None     # data_referencia do mercado_dia (ou do 1º resumo_acao); None em so_execucao
    metadados: dict                 # pacote -> {versao_contrato, chave, gerado_em, dados_ate} (da completa + execucao)
    operacoes: list
    avisos: list[str]
    def contagens(self) -> dict[str, int]
def planejar(entrada: Entrada, estado: Estado, forcar: bool = False) -> Plano
```
- **Modo:** sem completa → `so_execucao`. Com completa: U = maior D com status "completa"; U None ou D > U →
  `normal`; D == U → `republicacao`; D < U → `buraco`.
- **execucao** (todos os modos): cada registro de `entrada.execucao` é uma foto com chave (data_referencia, rodada):
  inserir se nova, atualizar se o hash mudou.
- **Fotos da completa** (resumo_acao, noticias_dia, mercado_dia, experimento_top, experimento_historico):
  - normal/republicação: inserir nova, nada se o hash é igual, FotoAtualizar se mudou;
  - buraco: só inserir as que faltam; as diferentes viram aviso.
- **series_diarias:** para cada série: `novos = compacto_para_pontos`, `antigos = estado.pontos.get(ticker, {})`.
  Sempre `CatalogoGravar(ticker, tipo, nome, list(valores.keys()))`.
  - **normal/republicação:**
    - `res = emenda.analisar(...)`;
    - cada novo: inserir se a data é nova; nada se o hash é igual; senão `PontoAtualizar`. O `anterior` é None
      quando todo campo que mudou (valor ou status) é explicado (`data in datas_explicadas[campo]`); senão é o
      antigo;
    - antigos com d0 ≤ data ≤ última nova e ausentes dos novos → `PontoRemover`;
    - antigos com data < d0 e campo em `fatores_aplicar`: valor × k (None continua None). Um `PontoAtualizar`
      por data, com `anterior=None`;
    - `AjusteRegistrar` para cada ajuste; avisos += res.avisos.
  - **buraco:** só `PontoInserir` das datas que faltam; contar as diferentes num aviso.
- **Versionadas:**
  - normal/republicação:
    - **trava:** abertas = chaves com versão `valido_ate None`. Se abertas > 0 e (len(registros) == 0 ou
      len(registros) < 0,8 × abertas) e não `forcar` → `ErroPublicacao` (mensagem cita `--forcar`);
    - para cada registro (h = hash_registro):
      - sem versão aberta: se a última versão tem `valido_ate == D`: hash igual → Reabrir; senão, se
        `valido_desde == D` → Corrigir + Reabrir; senão → Inserir `[D, None)`. Sem nenhuma versão → Inserir `[D, None)`;
      - aberta com hash igual → nada;
      - aberta com `valido_desde == D` → Corrigir;
      - senão → Fechar(D) + Inserir `[D, None)`;
    - chaves abertas ausentes da completa, ou presentes em `removidos` (dicts com os campos da chave) →
      Fechar(D).
  - buraco: para cada registro:
    - chave sem versões → Inserir `[D, próxima D "completa" > D]` (ou None se não houver);
    - D < 1ª.valido_desde: hash igual → Antecipar(1ª, D); diferente → Inserir `[D, 1ª.valido_desde)`;
    - senão nada.
- **`contagens()`:** conta as operações por rótulo:
  - `"<pacote>"` = FotoInserir; `"<pacote>_atualizadas"`;
  - `"series_pontos"` = PontoInserir; `"series_pontos_atualizados"`; `"series_pontos_removidos"`;
  - `"series_ajustes"` = AjusteRegistrar aplicado; `"series_recusas"` = AjusteRegistrar com classificacao "recusado";
  - `"<versionada>"` = VersaoInserir; `"<versionada>_fechadas"`; `"<versionada>_corrigidas"`.
  Só chaves com contagem > 0.

## publicador/gravador.py
```python
def criar_tabelas(conexao: Conexao) -> list[str]
def carregar_estado(conexao: Conexao, entrada: Entrada) -> Estado
def gravar(conexao: Conexao, entrada: Entrada, plano: Plano, inicio: datetime) -> None
def registrar_rejeicao(conexao: Conexao, entrada: Entrada, erros: list[str], inicio: datetime) -> None
def contar_tabelas(conexao: Conexao) -> dict[str, int]
```
- `criar_tabelas`: lê `sql/esquema.sql` (RAIZ), tira as linhas que começam com `--`, separa por `;`, troca `{p}` por
  `conexao.prefixo`, executa cada comando e confirma. Devolve os nomes das tabelas criadas.
- `carregar_estado`:
  - publicacoes (todas);
  - fotos: só as chaves com `data_referencia` presente nos pacotes da entrada (execucao inclusive), via `IN (...)`;
  - pontos: `removido_em IS NULL` dos tickers da completa;
  - catálogo;
  - versões de todos os registros das 3 versionadas (ordenadas por valido_desde);
  - recusas = vazio (o publicador não usa).
- `gravar`: `conexao.iniciar()`; executa as operações na ordem (`executar_muitos` para inserções em lote de pontos
  e fotos); upsert em `publicacoes`; `confirmar()`. Qualquer exceção → `desfazer()` e relança.
  - FotoInserir: colunas = campos da chave + data_publicacao (= D) + hash + registro (execucao: + status =
    registro["status"]). Para resumo_acao/noticias_dia, a chave (data_referencia, ticker).
  - FotoAtualizar/VersaoCorrigir/PontoAtualizar(anterior)/PontoRemover: INSERT em correcoes `(tabela base sem
    prefixo, chave JSON, data_troca=D, hash_anterior, conteudo_anterior, hash_novo)`. Remover: hash_novo = "removido"
    preenchido com zeros de 64 caracteres.
  - Ponto: colunas ticker, data, valores (JSON), status (JSON), hash, data_publicacao=D.
  - Atualizar ponto: `UPDATE ... SET valores, status, hash WHERE ticker AND data`.
  - CatalogoGravar: `INSERT ... ON DUPLICATE KEY UPDATE tipo=VALUES(tipo), nome=VALUES(nome), campos=VALUES(campos),
    primeira_publicacao=LEAST(primeira_publicacao, VALUES(primeira_publicacao)),
    ultima_publicacao=GREATEST(ultima_publicacao, VALUES(ultima_publicacao))`.
  - AjusteRegistrar: `INSERT ... ON DUPLICATE KEY UPDATE` de todos os campos (republicação substitui).
  - publicacoes: `INSERT ... ON DUPLICATE KEY UPDATE`. O status novo "completa" sempre vence; "so_execucao" e
    "rejeitada" não rebaixam um "completa" (`IF(status='completa','completa',VALUES(status))`). Também:
    `vezes_publicada = vezes_publicada + 1`, contagens_novas = `plano.contagens()`, `metadados = plano.metadados`
    (só se não nulo), `data_referencia` (só se não nula), hashes, pastas, `execucao_status` = status do último
    registro de execucao, inicio, fim = agora.
- `registrar_rejeicao`: transação própria, só o upsert em publicacoes (status "rejeitada", erros = lista).
- `contar_tabelas`: `SELECT COUNT(*) AS n FROM <t>` das 14.

## publicador/resumo.py
`formatar(plano: Plano, segundos: float) -> str`. Primeira linha
`"23/10/2026 publicado (modo normal, execução: ok) em 4,2 s"` (so_execucao: `"23/10/2026: só a execução foi
gravada (sem carga completa)"`). Depois, uma linha por grupo com contagem, alinhada com pontos, em formato
brasileiro (`44.671`), incluindo `"top 5 ... não publicado (pacote ausente)"` quando faltar `experimento_top` na
completa. Por fim, `Avisos:` com cada aviso.

## publicador/__main__.py
`python -m publicador (--criar-tabelas | --data AAAA-MM-DD [--forcar] | --conferir)`, exatamente um dos três.
- Carrega a config e abre a conexão "escrita".
- `--data`: inicio → `leitor.ler` → `validar`:
  - com erros: `registrar_rejeicao`, imprime os erros (no máximo 30) e sai 1;
  - senão: `carregar_estado` → `planejar` (ErroPublicacao → registrar_rejeicao + sai 1) → `gravar` → imprime o resumo.
- Saídas: 0 ok; 1 recusado/validação; 2 config, banco ou uso.
- Nunca imprime credencial.

## api/
- `api/main.py`: `criar_app(config: Config | None = None, fabrica_repositorio=None) -> FastAPI`. Sem config →
  `config.carregar()` na criação.
- `api/servidor.py`: `app = criar_app()` (para o uvicorn).
- `fabrica_repositorio(config)` devolve um context manager que entrega um Repositorio. O padrão abre
  `banco.conectar(cfg, "leitura")` e fecha no fim da requisição.
- `api/repositorio.py`: `class Repositorio(conexao)` com
  - `ultima_publicacao()`, `publicacao_ate(data)` → `{data_publicacao, data_referencia, metadados}` ou None
    (só status "completa");
  - `foto(pacote, data, ticker)` → `(registro, data_publicacao, data_referencia)` ou None:
    - com data: maior data_publicacao ≤ data, desempate pela maior data_referencia;
    - sem data: maior data_referencia;
  - `versoes(pacote, data, ticker)` → lista de registros válidos em data (None = abertas);
  - `ticker_existe(ticker)` (alguma versão em acoes);
  - `serie(ticker, data_fim)` → `(catalogo, pontos, recusas)` ou None. Pontos com data ≤ data_fim e removido_em
    nulo; recusas = por campo, o maior `ate` com classificacao "recusado";
  - `historico(ticker, campo, inicio, fim)` → lista `(data_referencia, valor_json)`;
  - `execucao_ultima()`, `execucoes(data)`;
  - `ping()`.
- `api/rotas.py`: endpoints do plano §d. Todas as respostas trazem `data_publicacao` e `data_referencia`, e os
  envelopes, os campos de `CAMPOS_ENVELOPE`.
- Chave: cabeçalho `X-API-Key` comparado com `secrets.compare_digest` contra `config.chaves_api`. Ausente, errada
  ou nenhuma configurada → 401 `{"detail":"chave de API ausente ou inválida"}`.
- `/v1/saude` sem chave.
- `ErroBanco` → 503.
- CORS: `CORSMiddleware(allow_origins=list(cors_origens), allow_methods=["GET"], allow_headers=["X-API-Key"])`.
