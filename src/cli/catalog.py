from decimal import Decimal, InvalidOperation

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from sqlalchemy import select

from src.cli.context import get_tenant
from src.db.models import (
    BOMLine,
    CatalogItem,
    CatalogModifier,
    InventoryItem,
    MappingGap,
)

app = typer.Typer()
console = Console()


@app.command()
def list():
    session, tenant = get_tenant()

    items = session.scalars(
        select(CatalogItem)
        .where(CatalogItem.tenant_id == tenant.id)
        .order_by(CatalogItem.category, CatalogItem.name)
    ).all()

    modifiers = session.scalars(
        select(CatalogModifier)
        .where(CatalogModifier.tenant_id == tenant.id)
        .order_by(CatalogModifier.category, CatalogModifier.name)
    ).all()

    if not items and not modifiers:
        console.print("[yellow]No catalog items or modifiers.[/yellow]")
        return

    if items:
        table = Table(title="Catalog Items")
        table.add_column("Name")
        table.add_column("Category")
        table.add_column("Price", justify="right")
        table.add_column("Active")

        for item in items:
            table.add_row(
                item.name,
                item.category,
                f"${item.sale_price}",
                "[green]✓[/green]" if item.is_active else "[red]✗[/red]",
            )
        console.print(table)

    if modifiers:
        table = Table(title="Modifiers")
        table.add_column("Name")
        table.add_column("Category")
        table.add_column("Active")

        for mod in modifiers:
            table.add_row(
                mod.name,
                mod.category,
                "[green]✓[/green]" if mod.is_active else "[red]✗[/red]",
            )
        console.print(table)


@app.command("add-modifier")
def add_modifier():
    session, tenant = get_tenant()

    name = Prompt.ask("Modifier name (e.g. Chocolate, Waffle Cone)")

    existing = session.scalar(
        select(CatalogModifier)
        .where(CatalogModifier.tenant_id == tenant.id)
        .where(CatalogModifier.name == name)
    )
    if existing:
        console.print(f"[red]'{name}' already exists.[/red]")
        return

    other_mods = session.scalars(
        select(CatalogModifier.category)
        .where(CatalogModifier.tenant_id == tenant.id)
        .distinct()
    ).all()

    if other_mods:
        console.print("\n[bold]Categories:[/bold]")
        for i, cat in enumerate(other_mods, 1):
            console.print(f"  [{i}] {cat}")
        console.print(f"  [{len(other_mods) + 1}] + New category")

        while True:
            try:
                idx = int(Prompt.ask("Category"))
                if 1 <= idx <= len(other_mods) + 1:
                    break
            except ValueError:
                pass
            console.print("[red]Invalid selection.[/red]")

        if idx <= len(other_mods):
            category = other_mods[idx - 1]
        else:
            category = Prompt.ask("New category name")
    else:
        category = Prompt.ask("Category (e.g. flavor, cone, topping)")

    modifier = CatalogModifier(
        tenant_id=tenant.id,
        name=name,
        category=category,
    )
    session.add(modifier)
    session.flush()

    # --- Auto-mapping from existing patterns ---

    inv_items = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant.id)
        .order_by(InventoryItem.name)
    ).all()

    if not inv_items:
        session.commit()
        console.print(f"[green]✓ Added modifier '{name}' ({category})[/green]")
        console.print(
            "[yellow]No inventory items yet -- map it manually with 'catalog map'.[/yellow]"
        )
        return

    console.print(f"\n  Which inventory item does '{name}' deplete?")
    for i, item in enumerate(inv_items, 1):
        console.print(f"  [{i}] {item.name} ({item.unit})")

    while True:
        try:
            idx = int(Prompt.ask("  Select item"))
            if 1 <= idx <= len(inv_items):
                break
        except ValueError:
            pass
        console.print("[red]  Invalid selection.[/red]")

    target_inv = inv_items[idx - 1]

    # Find a reference modifier in the same category to get the pattern
    reference_mod = session.scalar(
        select(CatalogModifier)
        .where(CatalogModifier.tenant_id == tenant.id)
        .where(CatalogModifier.category == category)
        .where(CatalogModifier.id != modifier.id)
    )

    if reference_mod:
        ref_bom = session.execute(
            select(BOMLine, CatalogItem)
            .join(CatalogItem, BOMLine.catalog_item_id == CatalogItem.id)
            .where(BOMLine.tenant_id == tenant.id)
            .where(BOMLine.catalog_modifier_id == reference_mod.id)
            .order_by(CatalogItem.name)
        ).all()

        if ref_bom:
            console.print(
                f"\n  [bold]Existing pattern for '{category}' modifiers:[/bold]"
            )
            for bom, cat_item in ref_bom:
                console.print(f"    {cat_item.name} → {bom.quantity} {bom.unit}")

            apply = Prompt.ask(
                f"\n  Apply same pattern for '{name}' → {target_inv.name}?",
                choices=["y", "n"],
                default="y",
            )

            if apply == "y":
                count = 0
                for bom, cat_item in ref_bom:
                    session.add(
                        BOMLine(
                            tenant_id=tenant.id,
                            catalog_item_id=cat_item.id,
                            catalog_modifier_id=modifier.id,
                            inventory_item_id=target_inv.id,
                            quantity=bom.quantity,
                            unit=target_inv.unit,
                        )
                    )
                    count += 1
                session.commit()
                console.print(
                    f"[green]✓ Added modifier '{name}' with {count} BOM mappings[/green]"
                )
                return

    # No pattern found — single manual mapping
    while True:
        try:
            qty = Decimal(Prompt.ask(f"  Quantity per sale ({target_inv.unit})"))
            if qty > 0:
                break
            console.print("[red]  Must be positive.[/red]")
        except InvalidOperation:
            console.print("[red]  Invalid number.[/red]")

    catalog_items = session.scalars(
        select(CatalogItem)
        .where(CatalogItem.tenant_id == tenant.id)
        .order_by(CatalogItem.name)
    ).all()

    if catalog_items:
        console.print("\n  Apply to which catalog items?")
        for i, item in enumerate(catalog_items, 1):
            console.print(f"  [{i}] {item.name} ({item.category})")

        apply_all = Prompt.ask("  Apply to all?", choices=["y", "n"], default="y")

        if apply_all == "y":
            targets = catalog_items
        else:
            indices = Prompt.ask("  Enter numbers separated by commas")
            targets = [catalog_items[int(x.strip()) - 1] for x in indices.split(",")]

        for cat_item in targets:
            session.add(
                BOMLine(
                    tenant_id=tenant.id,
                    catalog_item_id=cat_item.id,
                    catalog_modifier_id=modifier.id,
                    inventory_item_id=target_inv.id,
                    quantity=qty,
                    unit=target_inv.unit,
                )
            )

    session.commit()
    console.print(
        f"[green]✓ Added modifier '{name}' with {len(targets)} BOM mappings[/green]"
    )


@app.command("add-item")
def add_item():
    session, tenant = get_tenant()

    name = Prompt.ask("Item name (what the customer sees)")

    existing = session.scalar(
        select(CatalogItem)
        .where(CatalogItem.tenant_id == tenant.id)
        .where(CatalogItem.name == name)
    )
    if existing:
        console.print(f"[red]'{name}' already exists.[/red]")
        return

    other_items = session.scalars(
        select(CatalogItem.category)
        .where(CatalogItem.tenant_id == tenant.id)
        .distinct()
    ).all()

    if other_items:
        console.print("\n[bold]Categories:[/bold]")
        for i, cat in enumerate(other_items, 1):
            console.print(f"  [{i}] {cat}")
        console.print(f"  [{len(other_items) + 1}] + New category")

        while True:
            try:
                idx = int(Prompt.ask("Category"))
                if 1 <= idx <= len(other_items) + 1:
                    break
            except ValueError:
                pass
            console.print("[red]Invalid selection.[/red]")

        if idx <= len(other_items):
            category = other_items[idx - 1]
        else:
            category = Prompt.ask("New category name")
    else:
        category = Prompt.ask("Category (e.g. scoops, sundaes, drinks)")

    while True:
        try:
            price = Decimal(Prompt.ask("Sale price"))
            if price > 0:
                break
            console.print("[red]Must be positive.[/red]")
        except InvalidOperation:
            console.print("[red]Invalid number.[/red]")

    catalog_item = CatalogItem(
        tenant_id=tenant.id,
        name=name,
        category=category,
        sale_price=price,
    )
    session.add(catalog_item)
    session.flush()

    # --- Auto-mapping from existing patterns ---

    reference = session.scalar(
        select(CatalogItem)
        .where(CatalogItem.tenant_id == tenant.id)
        .where(CatalogItem.category == category)
        .where(CatalogItem.id != catalog_item.id)
    )

    if not reference:
        session.commit()
        console.print(f"[green]✓ Added catalog item '{name}' (${price})[/green]")
        console.print(
            "[yellow]First item in this category -- use 'catalog map' to set up BOM.[/yellow]"
        )
        return

    ref_bom = session.execute(
        select(BOMLine, CatalogModifier, InventoryItem)
        .join(InventoryItem, BOMLine.inventory_item_id == InventoryItem.id)
        .outerjoin(CatalogModifier, BOMLine.catalog_modifier_id == CatalogModifier.id)
        .where(BOMLine.tenant_id == tenant.id)
        .where(BOMLine.catalog_item_id == reference.id)
    ).all()

    if not ref_bom:
        session.commit()
        console.print(f"[green]✓ Added catalog item '{name}' (${price})[/green]")
        console.print(
            "[yellow]Reference item has no BOM -- use 'catalog map.'[/yellow]"
        )
        return

    console.print(f"\n  [bold]Existing BOM for '{reference.name}':[/bold]")
    for bom, mod, inv in ref_bom:
        mod_label = mod.name if mod else "always"
        console.print(f"    + {mod_label} → {bom.quantity} {bom.unit} of {inv.name}")

    apply = Prompt.ask(
        f"\n  Apply same mappings to '{name}'?",
        choices=["y", "n"],
        default="y",
    )

    if apply == "y":
        count = 0
        for bom, mod, inv in ref_bom:
            session.add(
                BOMLine(
                    tenant_id=tenant.id,
                    catalog_item_id=catalog_item.id,
                    catalog_modifier_id=bom.catalog_modifier_id,
                    inventory_item_id=bom.inventory_item_id,
                    quantity=bom.quantity,
                    unit=bom.unit,
                )
            )
            count += 1
        session.commit()
        console.print(
            f"[green]✓ Added '{name}' (${price}) with {count} BOM mappings[/green]"
        )
    else:
        session.commit()
        console.print(f"[green]✓ Added '{name}' (${price})[/green]")
        console.print("[yellow]Use 'catalog map' to set up BOM manually.[/yellow]")


@app.command()
def map():
    session, tenant = get_tenant()

    catalog_items = session.scalars(
        select(CatalogItem)
        .where(CatalogItem.tenant_id == tenant.id)
        .order_by(CatalogItem.name)
    ).all()

    if not catalog_items:
        console.print("[red]No catalog items. Add items first.[/red]")
        return

    console.print("\n[bold]Catalog items:[/bold]")
    for i, item in enumerate(catalog_items, 1):
        console.print(f"  [{i}] {item.name} ({item.category})")

    while True:
        try:
            idx = int(Prompt.ask("\nSelect catalog item"))
            if 1 <= idx <= len(catalog_items):
                break
        except ValueError:
            pass
        console.print("[red]Invalid selection.[/red]")

    catalog_item = catalog_items[idx - 1]

    modifiers = session.scalars(
        select(CatalogModifier)
        .where(CatalogModifier.tenant_id == tenant.id)
        .order_by(CatalogModifier.name)
    ).all()

    modifier = None
    if modifiers:
        console.print("\n[bold]Modifiers:[/bold]")
        for i, mod in enumerate(modifiers, 1):
            console.print(f"  [{i}] {mod.name} ({mod.category})")
        console.print(
            f"  [{len(modifiers) + 1}] Always (no modifier -- depletes on every sale e.g. spoons)"
        )

        while True:
            try:
                idx = int(Prompt.ask("Select modifier"))
                if 1 <= idx <= len(modifiers) + 1:
                    break
            except ValueError:
                pass
            console.print("[red]Invalid selection.[/red]")

        if idx <= len(modifiers):
            modifier = modifiers[idx - 1]
    else:
        console.print(
            "[yellow]No modifiers. This will be an 'always' depletion.[/yellow]"
        )

    inv_items = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant.id)
        .order_by(InventoryItem.name)
    ).all()

    if not inv_items:
        console.print("[red]No inventory items. Add inventory first.[/red]")
        return

    console.print("\n[bold]Inventory items:[/bold]")
    for i, item in enumerate(inv_items, 1):
        console.print(f"  [{i}] {item.name} ({item.unit})")

    while True:
        try:
            idx = int(Prompt.ask("Depletes which inventory item"))
            if 1 <= idx <= len(inv_items):
                break
        except ValueError:
            pass
        console.print("[red]Invalid selection.[/red]")

    inv_item = inv_items[idx - 1]

    while True:
        try:
            qty = Decimal(Prompt.ask(f"Quantity per sale ({inv_item.unit})"))
            if qty > 0:
                break
            console.print("[red]Must be positive.[/red]")
        except InvalidOperation:
            console.print("[red]Invalid number.[/red]")

    existing = session.scalar(
        select(BOMLine)
        .where(BOMLine.tenant_id == tenant.id)
        .where(BOMLine.catalog_item_id == catalog_item.id)
        .where(
            BOMLine.catalog_modifier_id == modifier.id
            if modifier
            else BOMLine.catalog_modifier_id.is_(None)
        )
        .where(BOMLine.inventory_item_id == inv_item.id)
    )

    if existing:
        mod_label = modifier.name if modifier else "always"
        console.print(
            f"[red]Mapping already exists: {catalog_item.name} + {mod_label} "
            f"→ {inv_item.name} ({existing.quantity} {existing.unit})[/red]"
        )
        return

    session.add(
        BOMLine(
            tenant_id=tenant.id,
            catalog_item_id=catalog_item.id,
            catalog_modifier_id=modifier.id if modifier else None,
            inventory_item_id=inv_item.id,
            quantity=qty,
            unit=inv_item.unit,
        )
    )
    session.commit()

    mod_label = modifier.name if modifier else "always"
    console.print(
        f"[green]✓ Mapped: {catalog_item.name} + {mod_label} "
        f"→ {qty} {inv_item.unit} of {inv_item.name}[/green]"
    )


@app.command("show-map")
def show_map():
    session, tenant = get_tenant()

    bom_lines = session.execute(
        select(BOMLine, CatalogItem, CatalogModifier, InventoryItem)
        .join(CatalogItem, BOMLine.catalog_item_id == CatalogItem.id)
        .outerjoin(CatalogModifier, BOMLine.catalog_modifier_id == CatalogModifier.id)
        .join(InventoryItem, BOMLine.inventory_item_id == InventoryItem.id)
        .where(BOMLine.tenant_id == tenant.id)
        .order_by(CatalogItem.name, CatalogModifier.name)
    ).all()

    if not bom_lines:
        console.print("[yellow]No BOM mappings yet.[/yellow]")
        return

    table = Table(title="BOM Mappings")
    table.add_column("Catalog Item")
    table.add_column("Modifier")
    table.add_column("Inventory Item")
    table.add_column("Quantity", justify="right")
    table.add_column("Unit")

    for bom, cat_item, mod, inv in bom_lines:
        table.add_row(
            cat_item.name,
            mod.name if mod else "(always)",
            inv.name,
            str(bom.quantity),
            bom.unit,
        )

    console.print(table)


@app.command("edit-map")
def edit_map():
    session, tenant = get_tenant()

    bom_lines = session.execute(
        select(BOMLine, CatalogItem, CatalogModifier, InventoryItem)
        .join(CatalogItem, BOMLine.catalog_item_id == CatalogItem.id)
        .outerjoin(CatalogModifier, BOMLine.catalog_modifier_id == CatalogModifier.id)
        .join(InventoryItem, BOMLine.inventory_item_id == InventoryItem.id)
        .where(BOMLine.tenant_id == tenant.id)
        .order_by(CatalogItem.name, CatalogModifier.name)
    ).all()

    if not bom_lines:
        console.print("[yellow]No BOM mappings to edit.[/yellow]")
        return

    for i, (bom, cat_item, mod, inv) in enumerate(bom_lines, 1):
        mod_label = mod.name if mod else "(always)"
        console.print(
            f"  [{i}] {cat_item.name} + {mod_label} → {bom.quantity} {bom.unit} of {inv.name}"
        )

    while True:
        try:
            idx = int(Prompt.ask("\nSelect mapping to edit"))
            if 1 <= idx <= len(bom_lines):
                break
        except ValueError:
            pass
        console.print("[red]Invalid selection.[/red]")

    bom, cat_item, mod, inv = bom_lines[idx - 1]
    mod_label = mod.name if mod else "(always)"

    console.print(
        f"\n  Current: {cat_item.name} + {mod_label} → {bom.quantity} {bom.unit} of {inv.name}"
    )

    while True:
        try:
            new_qty = Decimal(Prompt.ask(f"  New quantity ({bom.unit})"))
            if new_qty > 0:
                break
            console.print("[red]  Must be positive.[/red]")
        except InvalidOperation:
            console.print("[red]  Invalid number.[/red]")

    bom.quantity = new_qty
    session.commit()
    console.print(
        f"[green]✓ Updated: {cat_item.name} + {mod_label} → {new_qty} {bom.unit} of {inv.name}[/green]"
    )


@app.command("remove-map")
def remove_map():
    session, tenant = get_tenant()

    bom_lines = session.execute(
        select(BOMLine, CatalogItem, CatalogModifier, InventoryItem)
        .join(CatalogItem, BOMLine.catalog_item_id == CatalogItem.id)
        .outerjoin(CatalogModifier, BOMLine.catalog_modifier_id == CatalogModifier.id)
        .join(InventoryItem, BOMLine.inventory_item_id == InventoryItem.id)
        .where(BOMLine.tenant_id == tenant.id)
        .order_by(CatalogItem.name, CatalogModifier.name)
    ).all()

    if not bom_lines:
        console.print("[yellow]No BOM mappings to remove.[/yellow]")
        return

    for i, (bom, cat_item, mod, inv) in enumerate(bom_lines, 1):
        mod_label = mod.name if mod else "(always)"
        console.print(
            f"  [{i}] {cat_item.name} + {mod_label} → {bom.quantity} {bom.unit} of {inv.name}"
        )

    while True:
        try:
            idx = int(Prompt.ask("\nSelect mapping to remove"))
            if 1 <= idx <= len(bom_lines):
                break
        except ValueError:
            pass
        console.print("[red]Invalid selection.[/red]")

    bom, cat_item, mod, inv = bom_lines[idx - 1]
    mod_label = mod.name if mod else "(always)"

    confirm = Prompt.ask(
        f"  Remove {cat_item.name} + {mod_label} → {inv.name}?",
        choices=["y", "n"],
        default="n",
    )

    if confirm == "y":
        session.delete(bom)
        session.commit()
        console.print("[green]✓ Removed mapping[/green]")
    else:
        console.print("[yellow]Cancelled.[/yellow]")


@app.command()
def gaps():
    session, tenant = get_tenant()

    gap_rows = session.scalars(
        select(MappingGap)
        .where(MappingGap.tenant_id == tenant.id)
        .where(MappingGap.resolved.is_(False))
        .order_by(MappingGap.occurrence_count.desc())
    ).all()

    if not gap_rows:
        console.print("[green]No unmapped items. Everything is covered.[/green]")
        return

    table = Table(title="Unmapped Items in Sales")
    table.add_column("Item")
    table.add_column("Modifier")
    table.add_column("Source")
    table.add_column("Occurrences", justify="right")
    table.add_column("First Seen")

    for gap in gap_rows:
        table.add_row(
            gap.external_item_name,
            gap.external_modifier_name or "--",
            gap.source,
            str(gap.occurrence_count),
            gap.first_seen_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)
    console.print(
        "\n[yellow]Use 'catalog add-item', 'catalog add-modifier', "
        "and 'catalog map' to resolve these.[/yellow]"
    )
