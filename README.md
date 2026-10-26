# Patrulha 39

Protótipo acadêmico em Streamlit para apoiar o planejamento do policiamento preventivo orientado por dados na área da 39ª Cia PM. A aplicação transforma ocorrências históricas em focos de patrulhamento e permite comparar o cenário inicial com cenários em que viaturas ficam indisponíveis.

## O que a aplicação demonstra

- Leitura da base anonimizada e do limite territorial em GeoJSON.
- Associação das ocorrências a hexágonos regulares de 200 m entre vértices opostos.
- Pontuação por natureza (A=4, B=5, C=1, D=1, E=2 e F=3) e classificação dos hexágonos por percentis.
- Ordenação das 24 faixas horárias em três classes com oito faixas cada.
- Mapa com prioridades, ocorrências, focos das viaturas, áreas de patrulhamento e trechos que perdem cobertura após uma indisponibilidade.
- Um Cartão-Programa por viatura disponível, com foco, classe, raio e atividade sugerida em cada hora.
- Comparação da cobertura geométrica do cenário inicial com a cobertura após a redistribuição.
- Exportação dos Cartões-Programa para CSV.

## Arquitetura

O projeto é uma aplicação Python de arquivo único (`app.py`). O Streamlit controla a interface e os cenários; pandas lê e resume os registros; Shapely constrói e consulta as geometrias; pyproj projeta os dados para metros; Folium/Leaflet apresenta o mapa interativo; e streamlit-folium incorpora o mapa na página.

Fluxo de processamento:

1. Ler os registros e validar horário, coordenadas e código de natureza.
2. Projetar limite e pontos para SIRGAS 2000 / UTM 23S.
3. Construir a malha hexagonal e associar os pontos aos hexágonos.
4. Somar os pesos por hexágono e por hora, classificar as áreas e ordenar as horas.
5. Gerar os cartões conforme o tipo e a disponibilidade das viaturas.
6. Desenhar áreas indicativas de cobertura, compará-las e exibir a redistribuição.

## Metodologia

### Malha e prioridades

Os hexágonos usam raio de 100 m, equivalente a 200 m entre vértices opostos. Seus centros são posicionados em uma tesselação regular pointy-top. As células de borda são recortadas pelo limite territorial. Cada ocorrência dentro da área é associada a uma célula e soma o peso de sua natureza ao escore total e ao escore da hora correspondente. A interface informa quantos registros foram associados e quantos ficaram fora da malha.

As células com registros são separadas pelos percentis 50, 75 e 90 do escore total. Células sem registros recebem prioridade baixa. Essa é a regra de classificação deste protótipo; os pesos e percentis devem ser apresentados à banca como escolhas metodológicas da equipe.

As 24 horas são ordenadas pelo escore ponderado. As oito maiores formam a Classe 1, as oito seguintes a Classe 2 e as oito menores a Classe 3. Em empate, a hora mais cedo vem primeiro.

### Cartões e distribuição

Cada viatura disponível recebe uma indicação para cada faixa de uma hora:

- Classe 1: ponto-base de 20 minutos e patrulhamento de 40 minutos nas adjacências, até 1 km.
- Classe 2, posições 9 a 12: ponto-base de 20 minutos e patrulhamento de 40 minutos, até 1 km.
- Classe 2, posições 13 a 16: patrulhamento no entorno, até 2 km.
- Classe 3: patrulhamento no entorno, até 3 km. Quando uma hora da Classe 3 não tem registros, os candidatos vêm do conjunto de focos de referência da Classe 1, formado pelas quadrículas de maior incidência nessas faixas. O conjunto só é ampliado com os maiores escores territoriais se forem necessárias alternativas para alternar focos.

A heurística combina risco horário, dispersão dos focos e visitas anteriores. Em “Atendimento + prevenção”, a pontuação combina 80% de risco horário e 20% de dispersão; em “Policiamento preventivo”, combina 55% de risco e 45% de dispersão. A dispersão compara a distância entre focos das viaturas na mesma faixa, limitada a 2 km, e uma penalidade reduz células muito visitadas. Nas faixas com atividade de ponto-base da Classe 1 e da Classe 2, o algoritmo evita repetir focos entre faixas consecutivas e tenta manter focos distintos para as viaturas na mesma faixa. Se a área tiver menos células elegíveis do que viaturas, a exceção aparece no cartão.

### Cobertura e indisponibilidade

Para a faixa selecionada, a aplicação cria círculos de planejamento com o raio indicado para cada cartão, recorta as áreas ao limite territorial e calcula a união dos polígonos. A porcentagem é a área dessa união dividida pela área do território. Também conta os centros das células de alta prioridade que ficam fora desses polígonos.

Quando uma ou mais viaturas são marcadas como empenhadas, os cartões ativos são recalculados. O mapa compara a cobertura atual com o cenário inicial e destaca áreas antes cobertas que ficaram sem cobertura sugerida. Essa comparação é geométrica: não representa rotas, tempo de resposta, tráfego ou posição GPS.

## Estrutura de arquivos

```text
patrulha39/
├── app.py
├── requirements.txt
├── README.md
└── upload/
    ├── ocorrencias.txt
    └── 39_cia.geojson
```

## Tecnologias e licenciamento

O protótipo usa Python, Streamlit, pandas, Shapely, pyproj, Folium/Leaflet e dados cartográficos do OpenStreetMap. As faixas de versão aceitas estão em `requirements.txt`.

## Limitações e evolução

- O histórico descreve ocorrências passadas; o protótipo não prevê fatos futuros.
- Os raios são aproximações geométricas. Não são rotas de rua nem garantem cobertura operacional.
- Não há integração com GPS, rádio, despacho, escalas, trânsito ou localização em tempo real.
- A quantidade e os tipos de viatura são cenários configurados pelo usuário, não uma conexão com o efetivo real.
- Os pesos A–F são tratados como os valores fornecidos no desafio; a aplicação não tenta reidentificar as naturezas originais.
- Células recortadas na borda têm área menor dentro do limite analisado.

Evoluções possíveis: testar outras funções-objetivo, comparar cenários com métricas padronizadas, incluir custos de deslocamento em rede viária, permitir parâmetros de peso configuráveis e validar a heurística com especialistas da PMMG.

## Escopo

Este é um protótipo de prova de conceito para demonstração acadêmica. Os resultados são sugestões de planejamento e não substituem a avaliação ou decisão de profissionais da PMMG.
