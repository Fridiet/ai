from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import torch
from torch import nn
from torch.nn import functional as F


@dataclass(frozen=True)
class TwoTowerConfig:
    query_input_size: int
    recipe_input_size: int
    recipe_count: int
    hidden_size: int = 128
    embedding_size: int = 64
    recipe_id_embedding_size: int = 32
    dropout: float = 0.2
    temperature: float = 0.07

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TwoTowerConfig":
        return cls(**payload)


class QueryTower(nn.Module):
    def __init__(self, config: TwoTowerConfig) -> None:
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(config.query_input_size, config.hidden_size),
            nn.ReLU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.hidden_size, config.embedding_size),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return F.normalize(self.network(features), dim=-1)


class RecipeTower(nn.Module):
    def __init__(self, config: TwoTowerConfig) -> None:
        super().__init__()
        self.content_network = nn.Sequential(
            nn.Linear(config.recipe_input_size, config.hidden_size),
            nn.ReLU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.hidden_size, config.embedding_size),
        )
        self.recipe_embedding = nn.Embedding(
            config.recipe_count,
            config.recipe_id_embedding_size,
        )
        self.fusion = nn.Linear(
            config.embedding_size + config.recipe_id_embedding_size,
            config.embedding_size,
        )

    def forward(
        self,
        features: torch.Tensor,
        recipe_indices: torch.Tensor,
    ) -> torch.Tensor:
        content = self.content_network(features)
        identity = self.recipe_embedding(recipe_indices)
        combined = torch.cat((content, identity), dim=-1)
        return F.normalize(self.fusion(combined), dim=-1)


class HybridTwoTower(nn.Module):
    def __init__(self, config: TwoTowerConfig) -> None:
        super().__init__()
        self.config = config
        self.query_tower = QueryTower(config)
        self.recipe_tower = RecipeTower(config)

    def forward(
        self,
        query_features: torch.Tensor,
        recipe_features: torch.Tensor,
        recipe_indices: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        return (
            self.query_tower(query_features),
            self.recipe_tower(recipe_features, recipe_indices),
        )

    def in_batch_logits(
        self,
        query_features: torch.Tensor,
        recipe_features: torch.Tensor,
        recipe_indices: torch.Tensor,
    ) -> torch.Tensor:
        query_vectors, recipe_vectors = self(
            query_features,
            recipe_features,
            recipe_indices,
        )
        return torch.matmul(query_vectors, recipe_vectors.transpose(0, 1)) / float(
            self.config.temperature
        )
