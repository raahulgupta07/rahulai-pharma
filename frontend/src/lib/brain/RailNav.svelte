<script>
  let { active = $bindable('definitions'), onSelect = (sectionId, itemId) => {} } = $props();

  const groups = [
    {
      id: 'knowledge',
      label: 'KNOWLEDGE',
      items: [
        { id: 'definitions', label: 'Definitions' },
        { id: 'glossary', label: 'Glossary' },
        { id: 'patterns', label: 'Patterns' },
        { id: 'rules', label: 'Rules' },
        { id: 'graph', label: 'Graph' },
        { id: 'schema', label: 'Schema' },
        { id: 'org', label: 'Org' },
      ],
    },
    {
      id: 'ops',
      label: 'OPS',
      items: [
        { id: 'training', label: 'Training' },
        { id: 'datasource', label: 'Data Source' },
        { id: 'activity', label: 'Activity' },
      ],
    },
    {
      id: 'sharing',
      label: 'SHARING',
      items: [
        { id: 'promote', label: 'Promote ⤴' },
        { id: 'pull', label: 'Pull ⤓' },
        { id: 'conflicts', label: 'Conflicts ⚠' },
      ],
    },
    {
      id: 'activity',
      label: 'ACTIVITY',
      items: [
        { id: 'accesslog', label: 'Access Log' },
      ],
    },
  ];

  function handleClick(groupId, itemId) {
    active = itemId;
    onSelect(groupId, itemId);
  }
</script>

<nav class="brn-rail">
  {#each groups as group}
    <div class="brn-group">
      <span class="brn-group-label">{group.label}</span>
      {#each group.items as item}
        <button
          class="brn-item"
          class:brn-item--active={active === item.id}
          onclick={() => handleClick(group.id, item.id)}
          type="button"
        >
          {item.label}
        </button>
      {/each}
    </div>
  {/each}
</nav>

<style>
  .brn-rail {
    width: 220px;
    height: 100%;
    overflow-y: auto;
    padding-bottom: 80px;
    background-color: #f7f4ec;
    border-right: 1px solid #e3ddd0;
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
  }

  .brn-group {
    display: flex;
    flex-direction: column;
    margin-top: 20px;
  }

  .brn-group:first-child {
    margin-top: 12px;
  }

  .brn-group-label {
    font-size: 10px;
    font-weight: 600;
    color: #6b6557;
    letter-spacing: 0.06em;
    padding: 0 12px 4px 12px;
    text-transform: uppercase;
  }

  .brn-item {
    display: block;
    width: 100%;
    text-align: left;
    padding: 6px 12px;
    font-size: 13px;
    font-weight: 400;
    color: #2c2a26;
    background: transparent;
    border: none;
    border-left: 2px solid transparent;
    border-radius: 0;
    cursor: pointer;
    transition: background 0.1s ease, color 0.1s ease;
    box-sizing: border-box;
  }

  .brn-item:hover {
    background: rgba(201, 99, 66, 0.04);
  }

  .brn-item--active {
    background: rgba(201, 99, 66, 0.08);
    color: #c96342;
    font-weight: 600;
    border-left: 2px solid #c96342;
  }

  .brn-item--active:hover {
    background: rgba(201, 99, 66, 0.08);
  }
</style>
