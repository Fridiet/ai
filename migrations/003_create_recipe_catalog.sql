CREATE TABLE IF NOT EXISTS recipe_catalog (
    recipe_catalog_id BIGINT NOT NULL AUTO_INCREMENT,
    source_recipe_id VARCHAR(80) NOT NULL,
    recipe_name VARCHAR(150) NOT NULL,
    cook_time INT NULL,
    category VARCHAR(50) NULL,
    description VARCHAR(500) NULL,
    steps_json JSON NOT NULL,
    image_url VARCHAR(500) NULL,
    source VARCHAR(50) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (recipe_catalog_id),
    UNIQUE KEY uk_recipe_catalog_source_id (source, source_recipe_id),
    INDEX idx_recipe_catalog_name (recipe_name),
    INDEX idx_recipe_catalog_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS recipe_catalog_ingredients (
    recipe_catalog_ingredient_id BIGINT NOT NULL AUTO_INCREMENT,
    recipe_catalog_id BIGINT NOT NULL,
    ingredient_id BIGINT NOT NULL,
    canonical_name VARCHAR(120) NOT NULL,
    quantity DECIMAL(10,2) NULL,
    unit VARCHAR(10) NULL,
    optional TINYINT(1) NOT NULL DEFAULT 0,
    PRIMARY KEY (recipe_catalog_ingredient_id),
    UNIQUE KEY uk_recipe_catalog_ingredient (recipe_catalog_id, ingredient_id),
    INDEX idx_recipe_catalog_ing_id (ingredient_id),
    CONSTRAINT fk_recipe_catalog_ing_recipe
        FOREIGN KEY (recipe_catalog_id) REFERENCES recipe_catalog (recipe_catalog_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_recipe_catalog_ing_master
        FOREIGN KEY (ingredient_id) REFERENCES ingredient_master (ingredient_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
