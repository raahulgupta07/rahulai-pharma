<script>
  let { scope = $bindable('all'), onChange = (s) => {} } = $props();

  const segments = [
    { label: 'THIS AGENT', value: 'agent' },
    { label: 'COMPANY',    value: 'company' },
    { label: 'PERSONAL',   value: 'personal' },
    { label: 'ALL',        value: 'all' },
  ];

  function select(value) {
    scope = value;
    onChange(value);
  }
</script>

<div class="scsw-container">
  {#each segments as seg, i}
    {#if i > 0}
      <div class="scsw-divider"></div>
    {/if}
    <button
      class="scsw-segment {scope === seg.value ? 'scsw-active' : ''}"
      onclick={() => select(seg.value)}
      type="button"
    >
      {seg.label}
    </button>
  {/each}
</div>

<style>
  .scsw-container {
    display: inline-flex;
    align-items: stretch;
    border: 1px solid #e3ddd0;
    border-radius: 0;
    overflow: hidden;
    background: transparent;
  }

  .scsw-segment {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 6px 14px;
    background: transparent;
    border: none;
    border-radius: 0;
    color: #2c2a26;
    font-size: 11px;
    font-weight: 400;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    cursor: pointer;
    white-space: nowrap;
    transition: background 0.12s ease, color 0.12s ease;
    outline: none;
    line-height: 1;
  }

  .scsw-segment:hover:not(.scsw-active) {
    background: rgba(201, 99, 66, 0.06);
  }

  .scsw-segment:focus-visible {
    outline: 2px solid #c96342;
    outline-offset: -2px;
  }

  .scsw-segment.scsw-active {
    background: #c96342;
    color: #ffffff;
    font-weight: 600;
  }

  .scsw-divider {
    width: 1px;
    background: #e3ddd0;
    flex-shrink: 0;
  }
</style>
