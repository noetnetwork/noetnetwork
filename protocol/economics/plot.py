"""Optional chart renderer; calculations remain dependency-free."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from simulate import schedule, miner
from tokenomics import ALLOCATIONS

plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
fig, axes = plt.subplots(1, 3, figsize=(16,5.5))
fig.patch.set_facecolor('#f7f6f2')
for ax in axes: ax.set_facecolor('#f7f6f2')
for rate,color in [(10,'#8f99aa'),(12.5,'#6f8f5a'),(15,'#354acc'),(20,'#bf7958')]:
    rows=schedule(rate,30)
    axes[0].plot([r['year'] for r in rows],[r['mining_emission']/1e6 for r in rows],label=f'{rate}% reserve release',color=color,lw=2)
axes[0].set(title='Annual mining emissions',xlabel='Model year',ylabel='Million NOET / year')
axes[0].legend(frameon=False,fontsize=8)
r=schedule(years=30)
axes[1].plot([0]+[x['year'] for x in r],[20]+[x['minted_supply']/1e6 for x in r],color='#354acc',label='Minted, including locked')
axes[1].plot([0]+[x['year'] for x in r],[sum(a['initialCoins'] for a in ALLOCATIONS)/1e6]+[x['unlocked_upper_bound']/1e6 for x in r],color='#bf7958',label='Unlocked upper bound')
axes[1].axhline(100,color='#8f99aa',ls=':',label='100m cap')
axes[1].set(title='Recommended supply schedule',xlabel='Model year',ylabel='Million NOET')
axes[1].legend(frameon=False,fontsize=8,loc='lower right')
prices=[i/1000 for i in range(1001)]
for cost,u,label,color,ls in [(.94,0,'Rental, no paid work','#354acc','-'),(.94,.65,'Rental, 65% paid utilization','#354acc','--'),(.18,0,'Owned, no paid work','#bf7958','-'),(.18,.65,'Owned, 65% paid utilization','#bf7958','--')]:
    axes[2].plot(prices,[miner(1,1000,p,cost,u)['profit_per_day_usd'] for p in prices],label=label,color=color,ls=ls)
axes[2].axhline(0,color='#8f99aa',lw=1)
axes[2].set(title='Expected miner net per day',xlabel='Hypothetical NOET price (USD)',ylabel='USD / day')
axes[2].legend(frameon=False,fontsize=7,loc='upper left')
fig.suptitle('NOET | Tokenomics proposal',x=.055,ha='left',fontsize=20,fontweight='bold')
fig.text(.055,.02,'Illustrative model, not a forecast. Year 1; 1,000 equal workers; 2% pool fee. Paid-demand, GPU costs and proof overhead are assumptions.',fontsize=9,color='#555555')
fig.tight_layout(rect=[.02,.06,1,.93])
out=Path(__file__).parent/'results'
fig.savefig(out/'economics.png',dpi=160,facecolor=fig.get_facecolor())
fig.savefig(out/'economics.svg',facecolor=fig.get_facecolor())
