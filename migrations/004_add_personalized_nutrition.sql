ALTER TABLE recipe_catalog
    ADD COLUMN serving_size_g DECIMAL(10,2) NULL AFTER source,
    ADD COLUMN calories_kcal DECIMAL(10,2) NULL AFTER serving_size_g,
    ADD COLUMN carbohydrate_g DECIMAL(10,2) NULL AFTER calories_kcal,
    ADD COLUMN protein_g DECIMAL(10,2) NULL AFTER carbohydrate_g,
    ADD COLUMN fat_g DECIMAL(10,2) NULL AFTER protein_g,
    ADD COLUMN sodium_mg DECIMAL(10,2) NULL AFTER fat_g,
    ADD COLUMN nutrition_source VARCHAR(80) NULL AFTER sodium_mg;

CREATE TABLE IF NOT EXISTS user_diet_profiles (
    user_id BIGINT NOT NULL,
    goal VARCHAR(32) NOT NULL DEFAULT 'GENERAL',
    conditions_json JSON NULL,
    calorie_min DECIMAL(10,2) NULL,
    calorie_max DECIMAL(10,2) NULL,
    carbohydrate_min_g DECIMAL(10,2) NULL,
    carbohydrate_max_g DECIMAL(10,2) NULL,
    protein_min_g DECIMAL(10,2) NULL,
    sodium_max_mg DECIMAL(10,2) NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id),
    CONSTRAINT fk_user_diet_profile_user
        FOREIGN KEY (user_id) REFERENCES users (user_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS user_excluded_ingredients (
    user_id BIGINT NOT NULL,
    ingredient_id BIGINT NOT NULL,
    reason VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, ingredient_id),
    INDEX idx_user_excluded_ingredient_id (ingredient_id),
    CONSTRAINT fk_user_excluded_ingredient_user
        FOREIGN KEY (user_id) REFERENCES users (user_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_user_excluded_ingredient_master
        FOREIGN KEY (ingredient_id) REFERENCES ingredient_master (ingredient_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
